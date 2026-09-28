from __future__ import annotations

import inspect as python_inspect
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect as sqlalchemy_inspect, text

from core import catalog_schema as manifest
from core import catalog_capability_producers as capability_producers
from core.catalog_capability_producers import CatalogCapabilityPolicy


def _policy(
    *,
    issue_checker,
    minimum_revision: str | None = None,
    revision_aware: bool = False,
    supported_dialects: frozenset[str] | None = frozenset({"sqlite"}),
) -> CatalogCapabilityPolicy:
    return CatalogCapabilityPolicy(
        minimum_revision=(
            minimum_revision or manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION
        ),
        required_tables=frozenset({"extension_probe"}),
        supported_dialects=supported_dialects,
        capability_label="Capability Probe",
        minimum_revision_issue=("catalog is not at a known pre-0036 or 0036 revision"),
        inspection_error_prefix="Capability Probe schema inspection failed",
        issue_checker=issue_checker,
        revision_aware=revision_aware,
    )


def _extension_engine(path: Path):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('0039_answer_evidence_facts')")
        )
        connection.execute(text("CREATE TABLE extension_probe (id INTEGER PRIMARY KEY)"))
    return engine


def test_registered_capability_policy_runs_through_the_public_inspector(
    tmp_path: Path,
) -> None:
    name = "extension_probe_test"
    engine = _extension_engine(tmp_path / "extension-probe.db")
    observed_revisions: list[str] = []

    def issue_checker(_connection, revision):
        observed_revisions.append(revision)
        return ()

    try:
        manifest.register_catalog_capability_producer(
            name,
            _policy(issue_checker=issue_checker, revision_aware=True),
        )
        assert manifest.inspect_catalog_capability(name, engine) == ("ready", ())
        assert observed_revisions == [manifest.ANSWER_EVIDENCE_FACTS_REVISION]
        with pytest.raises(ValueError, match="already registered"):
            manifest.register_catalog_capability_producer(
                name,
                _policy(issue_checker=lambda _connection: ()),
            )
    finally:
        manifest.unregister_catalog_capability_producer(name)
        engine.dispose()


def test_registered_capability_can_delegate_dialect_validation_to_its_domain_checker(
    tmp_path: Path,
) -> None:
    name = "domain_dialect_probe_test"
    engine = _extension_engine(tmp_path / "domain-dialect-probe.db")
    try:
        manifest.register_catalog_capability_producer(
            name,
            _policy(
                issue_checker=lambda _connection: (),
                supported_dialects=None,
            ),
        )
        assert manifest.inspect_catalog_capability(name, engine) == ("ready", ())
    finally:
        manifest.unregister_catalog_capability_producer(name)
        engine.dispose()


def test_automation_inspector_dispatches_through_the_shared_producer_registry() -> None:
    source = python_inspect.getsource(manifest.inspect_enterprise_automation_workflows_capability)
    assert 'return inspect_catalog_capability("automation_workflows", bind)' in source
    assert "def inspect_connection" not in source
    assert (
        manifest._knowledge_serving_original_automation_capability
        is not manifest.inspect_enterprise_automation_workflows_capability
    )
    frozen_source = python_inspect.getsource(
        manifest._knowledge_serving_original_automation_capability
    )
    assert "Frozen pre-registry Stage25 Automation capability inspector" in frozen_source


def test_content_recovery_inspector_dispatches_through_the_shared_producer_registry() -> None:
    source = python_inspect.getsource(manifest.inspect_enterprise_content_recovery_capability)
    assert 'return inspect_catalog_capability("content_recovery", bind)' in source
    assert "def inspect_connection" not in source
    assert (
        manifest._knowledge_serving_original_content_recovery_capability
        is not manifest.inspect_enterprise_content_recovery_capability
    )
    policy_source = python_inspect.getsource(
        manifest._knowledge_serving_original_content_recovery_capability
    )
    assert "Frozen pre-registry Stage 23 Content Recovery capability inspector" in policy_source


@pytest.mark.parametrize(
    ("inspector", "original_name", "producer_name", "label"),
    [
        (
            manifest.inspect_enterprise_knowledge_serving_reliability_capability,
            "_knowledge_serving_original_knowledge_serving_reliability_capability",
            "knowledge_serving_reliability",
            "Stage26 Knowledge Serving",
        ),
        (
            manifest.inspect_enterprise_knowledge_operations_feedback_capability,
            "_knowledge_serving_original_knowledge_operations_feedback_capability",
            "knowledge_operations_feedback",
            "Stage27 Knowledge Operations",
        ),
    ],
)
def test_stage26_and_stage27_inspectors_dispatch_through_the_shared_producer_registry(
    inspector,
    original_name: str,
    producer_name: str,
    label: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = python_inspect.getsource(inspector)
    assert f'return inspect_catalog_capability("{producer_name}", bind)' in source
    assert "def inspect_connection" not in source
    original = getattr(manifest, original_name)
    assert original is not inspector
    assert label in python_inspect.getsource(original)
    assert producer_name in manifest._CATALOG_CAPABILITY_PRODUCERS.names()
    observed: list[tuple[str, object]] = []
    monkeypatch.setattr(
        manifest,
        "inspect_catalog_capability",
        lambda name, bind: observed.append((name, bind)) or ("ready", ()),
    )
    bind = object()
    assert inspector(bind) == ("ready", ())
    assert observed == [(producer_name, bind)]


def test_task_operations_inspector_dispatches_through_the_shared_producer_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = python_inspect.getsource(manifest.inspect_enterprise_task_operations_capability)
    assert 'return inspect_catalog_capability("task_operations", bind)' in source
    assert "def inspect_connection" not in source
    assert "task_operations" in manifest._CATALOG_CAPABILITY_PRODUCERS.names()
    observed: list[tuple[str, object]] = []
    monkeypatch.setattr(
        manifest,
        "inspect_catalog_capability",
        lambda name, bind: observed.append((name, bind)) or ("ready", ()),
    )
    bind = object()
    assert manifest.inspect_enterprise_task_operations_capability(bind) == ("ready", ())
    assert observed == [("task_operations", bind)]


def test_release_inspector_dispatches_through_the_shared_producer_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = python_inspect.getsource(manifest.inspect_enterprise_knowledge_base_release_capability)
    assert 'return inspect_catalog_capability("knowledge_base_releases", bind)' in source
    assert "def inspect_connection" not in source
    assert "knowledge_base_releases" in manifest._CATALOG_CAPABILITY_PRODUCERS.names()
    observed: list[tuple[str, object]] = []
    monkeypatch.setattr(
        manifest,
        "inspect_catalog_capability",
        lambda name, bind: observed.append((name, bind)) or ("ready", ()),
    )
    bind = object()
    assert manifest.inspect_enterprise_knowledge_base_release_capability(bind) == ("ready", ())
    assert observed == [("knowledge_base_releases", bind)]


def test_release_quality_certification_inspector_dispatches_through_shared_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = python_inspect.getsource(
        manifest.inspect_enterprise_release_quality_certification_capability
    )
    assert 'return inspect_catalog_capability("release_quality_certification", bind)' in source
    assert "def inspect_connection" not in source
    assert "release_quality_certification" in manifest._CATALOG_CAPABILITY_PRODUCERS.names()
    observed: list[tuple[str, object]] = []
    monkeypatch.setattr(
        manifest,
        "inspect_catalog_capability",
        lambda name, bind: observed.append((name, bind)) or ("ready", ()),
    )
    bind = object()
    assert manifest.inspect_enterprise_release_quality_certification_capability(bind) == (
        "ready",
        (),
    )
    assert observed == [("release_quality_certification", bind)]


def test_release_quality_operations_inspector_dispatches_through_shared_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = python_inspect.getsource(
        manifest.inspect_enterprise_release_quality_operations_capability
    )
    assert 'return inspect_catalog_capability("release_quality_operations", bind)' in source
    assert "def inspect_connection" not in source
    assert "release_quality_operations" in manifest._CATALOG_CAPABILITY_PRODUCERS.names()
    observed: list[tuple[str, object]] = []
    monkeypatch.setattr(
        manifest,
        "inspect_catalog_capability",
        lambda name, bind: observed.append((name, bind)) or ("ready", ()),
    )
    bind = object()
    assert manifest.inspect_enterprise_release_quality_operations_capability(bind) == (
        "ready",
        (),
    )
    assert observed == [("release_quality_operations", bind)]


def test_notification_center_inspector_dispatches_through_shared_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = python_inspect.getsource(manifest.inspect_enterprise_notification_center_capability)
    assert 'return inspect_catalog_capability("notification_center", bind)' in source
    assert "def inspect_connection" not in source
    assert "notification_center" in manifest._CATALOG_CAPABILITY_PRODUCERS.names()
    observed: list[tuple[str, object]] = []
    monkeypatch.setattr(
        manifest,
        "inspect_catalog_capability",
        lambda name, bind: observed.append((name, bind)) or ("ready", ()),
    )
    bind = object()
    assert manifest.inspect_enterprise_notification_center_capability(bind) == ("ready", ())
    assert observed == [("notification_center", bind)]


def test_registry_inspector_dispatches_through_shared_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = python_inspect.getsource(
        manifest.inspect_enterprise_knowledge_base_registry_capability
    )
    assert 'return inspect_catalog_capability("knowledge_base_registry", bind)' in source
    assert "def inspect_connection" not in source
    assert "knowledge_base_registry" in manifest._CATALOG_CAPABILITY_PRODUCERS.names()
    observed: list[tuple[str, object]] = []
    monkeypatch.setattr(
        manifest,
        "inspect_catalog_capability",
        lambda name, bind: observed.append((name, bind)) or ("ready", ()),
    )
    bind = object()
    assert manifest.inspect_enterprise_knowledge_base_registry_capability(bind) == ("ready", ())
    assert observed == [("knowledge_base_registry", bind)]


def test_workspace_authorization_inspector_dispatches_through_shared_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = python_inspect.getsource(manifest.inspect_workspace_authorization_capability)
    assert 'return inspect_catalog_capability("workspace_authorization", bind)' in source
    assert "def inspect_connection" not in source
    assert "workspace_authorization" in manifest._CATALOG_CAPABILITY_PRODUCERS.names()
    observed: list[tuple[str, object]] = []
    monkeypatch.setattr(
        manifest,
        "inspect_catalog_capability",
        lambda name, bind: observed.append((name, bind)) or ("ready", ()),
    )
    bind = object()
    assert manifest.inspect_workspace_authorization_capability(bind) == ("ready", ())
    assert observed == [("workspace_authorization", bind)]


def test_release_builtin_policy_is_reserved_from_public_replacement() -> None:
    policy = _policy(issue_checker=lambda _connection: ())
    with pytest.raises(ValueError, match="reserved"):
        manifest.register_catalog_capability_producer("knowledge_base_releases", policy)
    with pytest.raises(ValueError, match="reserved"):
        manifest.unregister_catalog_capability_producer("knowledge_base_releases")


def test_release_builtin_registry_rejects_raw_replacement() -> None:
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory("knowledge_base_releases")
    registry.register("knowledge_base_releases", lambda _bind: ("ready", ()), replace=True)
    try:
        with pytest.raises(TypeError, match="built-in.*contract was replaced"):
            manifest.inspect_catalog_capability("knowledge_base_releases", object())
    finally:
        registry.register("knowledge_base_releases", builtin, replace=True)


def test_release_builtin_registry_rejects_raw_removal() -> None:
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory("knowledge_base_releases")
    registry.unregister("knowledge_base_releases")
    try:
        with pytest.raises(TypeError, match="built-in.*was unregistered"):
            manifest.inspect_catalog_capability("knowledge_base_releases", object())
    finally:
        registry.register("knowledge_base_releases", builtin)


def test_builtin_capability_registry_rejects_raw_task_operations_replacement() -> None:
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory("task_operations")
    registry.register("task_operations", lambda _bind: ("ready", ()), replace=True)
    try:
        with pytest.raises(TypeError, match="built-in.*contract was replaced"):
            manifest.inspect_catalog_capability("task_operations", object())
    finally:
        registry.register("task_operations", builtin, replace=True)


def test_builtin_capability_registry_rejects_raw_task_operations_removal() -> None:
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory("task_operations")
    registry.unregister("task_operations")
    try:
        with pytest.raises(TypeError, match="built-in.*was unregistered"):
            manifest.inspect_catalog_capability("task_operations", object())
    finally:
        registry.register("task_operations", builtin)


def _content_recovery_revision_engine(path: Path, revisions: tuple[str, ...], tables=()):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        for revision in revisions:
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": revision},
            )
        for table in tables:
            connection.execute(text(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)'))
    return engine


def _legacy_content_recovery_capability_result(bind):
    return manifest._knowledge_serving_original_content_recovery_capability(bind)


def _legacy_automation_capability_result(bind):
    return manifest._knowledge_serving_original_automation_capability(bind)


def _stage26_27_revision_engine(
    path: Path,
    revisions: tuple[str, ...],
    tables: tuple[str, ...] = (),
    *,
    create_revision_table: bool = True,
):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as connection:
        if create_revision_table:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
            )
            for revision in revisions:
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                    {"revision": revision},
                )
        for table in tables:
            connection.execute(text(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)'))
    return engine


@pytest.mark.parametrize(
    ("inspector", "original", "capability_tables", "minimum_revision", "revision_issue"),
    [
        (
            manifest.inspect_enterprise_knowledge_serving_reliability_capability,
            manifest._knowledge_serving_original_knowledge_serving_reliability_capability,
            manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES,
            manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
            "catalog is not at a known pre-0036 or 0036 revision",
        ),
        (
            manifest.inspect_enterprise_knowledge_operations_feedback_capability,
            manifest._knowledge_serving_original_knowledge_operations_feedback_capability,
            manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_TABLES,
            manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REVISION,
            "catalog is not at a known pre-0037 or 0037 revision",
        ),
    ],
)
@pytest.mark.parametrize(
    ("case", "revisions", "tables", "create_revision_table"),
    [
        (
            "pre-minimum-empty",
            (manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,),
            (),
            True,
        ),
        (
            "pre-minimum-partial",
            (manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,),
            ("capability_placeholder",),
            True,
        ),
        ("minimum-empty", (), (), True),
        ("minimum-partial", (), ("capability_placeholder",), True),
        ("later-empty", (manifest.ANSWER_EVIDENCE_FACTS_REVISION,), (), True),
        (
            "later-partial",
            (manifest.ANSWER_EVIDENCE_FACTS_REVISION,),
            ("capability_placeholder",),
            True,
        ),
        ("unknown-empty", ("unknown_catalog_revision",), (), True),
        (
            "unknown-partial",
            ("unknown_catalog_revision",),
            ("capability_placeholder",),
            True,
        ),
        ("revision-table-absent", (), (), False),
        ("revision-table-absent-partial", (), ("capability_placeholder",), False),
        ("revision-table-empty", (), (), True),
        ("revision-value-empty", ("",), (), True),
        (
            "multiple-revisions",
            (
                manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
                manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
            ),
            (),
            True,
        ),
        (
            "multiple-revisions-partial",
            (
                manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
                manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
            ),
            ("capability_placeholder",),
            True,
        ),
        ("revision-table-empty-partial", (), ("capability_placeholder",), True),
        ("revision-value-empty-partial", ("",), ("capability_placeholder",), True),
    ],
)
def test_stage26_and_stage27_policies_preserve_the_legacy_revision_matrix(
    tmp_path: Path,
    inspector,
    original,
    capability_tables: frozenset[str],
    minimum_revision: str,
    revision_issue: str,
    case: str,
    revisions: tuple[str, ...],
    tables: tuple[str, ...],
    create_revision_table: bool,
) -> None:
    actual_tables = tuple(
        sorted(capability_tables)[:1] if tables == ("capability_placeholder",) else tables
    )
    actual_revisions = (
        (minimum_revision,) if case in {"minimum-empty", "minimum-partial"} else revisions
    )
    engine = _stage26_27_revision_engine(
        tmp_path / f"{minimum_revision}-{case}.db",
        actual_revisions,
        actual_tables,
        create_revision_table=create_revision_table,
    )
    try:
        expected = original(engine)
        actual = inspector(engine)
        assert actual == expected
        if case == "pre-minimum-empty":
            assert actual == ("not_available", ())
        elif case == "pre-minimum-partial":
            assert actual == ("unavailable", (revision_issue,))
        elif case in {"minimum-empty", "minimum-partial", "later-empty", "later-partial"}:
            missing = tuple(
                f"missing table {name}"
                for name in sorted(capability_tables - set(actual_tables))
            )
            assert actual == ("unavailable", missing)
        elif case in {
            "unknown-empty",
            "unknown-partial",
            "revision-table-absent",
            "revision-table-absent-partial",
            "revision-table-empty",
            "revision-table-empty-partial",
            "revision-value-empty",
            "revision-value-empty-partial",
        }:
            assert actual == ("unavailable", (revision_issue,))
        elif case in {"multiple-revisions", "multiple-revisions-partial"}:
            assert actual == ("unavailable", ("alembic_version contains multiple revisions",))
        if case in {
            "pre-minimum-empty",
            "pre-minimum-partial",
            "unknown-empty",
            "unknown-partial",
            "revision-table-absent",
            "revision-table-absent-partial",
            "revision-table-empty",
            "revision-table-empty-partial",
            "revision-value-empty",
            "revision-value-empty-partial",
            "multiple-revisions",
            "multiple-revisions-partial",
        }:
            assert actual == original(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("inspector", "error_prefix", "missing_table_name"),
    [
        (
            manifest.inspect_enterprise_knowledge_serving_reliability_capability,
            "Knowledge Serving schema inspection failed",
            "knowledge-serving-fallback-error.db",
        ),
        (
            manifest.inspect_enterprise_knowledge_operations_feedback_capability,
            "Knowledge Operations schema inspection failed",
            "knowledge-operations-fallback-error.db",
        ),
    ],
)
def test_stage26_and_stage27_policies_preserve_probe_and_fallback_error_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inspector,
    error_prefix: str,
    missing_table_name: str,
) -> None:
    def fail_probe(_connection):
        raise RuntimeError("probe failed")

    probe_engine = _stage26_27_revision_engine(
        tmp_path / f"{missing_table_name}.probe.db",
        (),
        create_revision_table=False,
    )
    monkeypatch.setattr(capability_producers, "inspect", fail_probe)
    assert inspector(probe_engine) == ("unavailable", (f"{error_prefix}: RuntimeError",))
    monkeypatch.setattr(capability_producers, "inspect", sqlalchemy_inspect)
    probe_engine.dispose()

    engine = _stage26_27_revision_engine(
        tmp_path / missing_table_name,
        (),
        create_revision_table=False,
    )
    calls = 0

    def fail_legacy_fallback(_bind, _inspector):
        nonlocal calls
        calls += 1
        raise RuntimeError("fallback failed")

    monkeypatch.setattr(manifest, "_schema_connection", fail_legacy_fallback)
    try:
        assert inspector(engine) == ("unavailable", (f"{error_prefix}: RuntimeError",))
        assert calls == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "name", ["knowledge_serving_reliability", "knowledge_operations_feedback"]
)
def test_stage26_and_stage27_builtin_registry_rejects_raw_replacement_and_removal(name: str) -> None:
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory(name)
    registry.register(name, lambda _bind: ("ready", ()), replace=True)
    try:
        with pytest.raises(TypeError, match="built-in.*contract was replaced"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin, replace=True)

    registry.unregister(name)
    try:
        with pytest.raises(TypeError, match="built-in.*was unregistered"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin)


def _release_revision_engine(
    path: Path,
    revisions: tuple[str, ...],
    tables: tuple[str, ...] = (),
    *,
    create_revision_table: bool = True,
):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as connection:
        if create_revision_table:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
            )
            for revision in revisions:
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                    {"revision": revision},
                )
        for table in tables:
            connection.execute(text(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)'))
    return engine


def _legacy_release_capability_result(bind):
    return manifest._knowledge_serving_revision_compatible(
        bind,
        manifest._KNOWLEDGE_SERVING_ORIGINAL_RELEASE_CAPABILITY,
        manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES,
        manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,
        manifest._enterprise_knowledge_base_release_capability_issues,
    )


def _legacy_release_quality_capability_result(bind):
    return manifest._knowledge_serving_revision_compatible(
        bind,
        manifest._KNOWLEDGE_SERVING_ORIGINAL_QUALITY_CAPABILITY,
        manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES,
        manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
        manifest._enterprise_release_quality_certification_capability_issues,
    )


def _legacy_release_quality_operations_capability_result(bind):
    return manifest._knowledge_serving_revision_compatible(
        bind,
        manifest._KNOWLEDGE_SERVING_ORIGINAL_QUALITY_OPERATIONS_CAPABILITY,
        manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES,
        manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
        manifest._enterprise_release_quality_operations_capability_issues,
    )


def _legacy_notification_capability_result(bind):
    return manifest._knowledge_serving_revision_compatible(
        bind,
        manifest._KNOWLEDGE_SERVING_ORIGINAL_NOTIFICATION_CAPABILITY,
        manifest.ENTERPRISE_NOTIFICATION_CENTER_TABLES,
        manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,
        manifest._enterprise_notification_center_capability_issues,
    )


def _legacy_registry_capability_result(bind):
    return manifest._knowledge_serving_revision_compatible(
        bind,
        manifest._KNOWLEDGE_SERVING_ORIGINAL_REGISTRY_CAPABILITY,
        manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES,
        manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,
        lambda connection, revision: manifest._knowledge_base_registry_capability_issues(
            connection,
            approval_action_revision=revision,
        ),
        revision_aware=True,
    )


def _legacy_workspace_authorization_capability_result(bind):
    return manifest._knowledge_serving_revision_compatible(
        bind,
        manifest._KNOWLEDGE_SERVING_ORIGINAL_WORKSPACE_AUTHORIZATION_CAPABILITY,
        manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES,
        manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,
        lambda connection, revision: manifest._workspace_authorization_capability_issues(
            connection,
            approval_action_revision=revision,
        ),
        revision_aware=True,
    )


@pytest.mark.parametrize(
    ("case", "revisions", "tables", "create_revision_table"),
    [
        (
            "pre-minimum-empty",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,),
            (),
            True,
        ),
        (
            "pre-minimum-partial",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,),
            (sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES)[0],),
            True,
        ),
        (
            "minimum-empty",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,),
            (),
            True,
        ),
        (
            "later-empty",
            (manifest.ANSWER_EVIDENCE_FACTS_REVISION,),
            (),
            True,
        ),
        (
            "minimum-partial",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,),
            (sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES)[0],),
            True,
        ),
        (
            "later-partial",
            (manifest.ANSWER_EVIDENCE_FACTS_REVISION,),
            (sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES)[0],),
            True,
        ),
        ("unknown-empty", ("unknown_catalog_revision",), (), True),
        (
            "unknown-partial",
            ("unknown_catalog_revision",),
            (sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES)[0],),
            True,
        ),
        ("revision-table-absent", (), (), False),
        (
            "revision-table-absent-partial",
            (),
            (sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES)[0],),
            False,
        ),
        ("revision-table-empty", (), (), True),
        ("revision-value-empty", ("",), (), True),
        (
            "multiple-revisions",
            (
                manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,
                manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,
            ),
            (),
            True,
        ),
    ],
)
def test_release_policy_preserves_the_legacy_revision_compatibility_matrix(
    tmp_path: Path,
    case: str,
    revisions: tuple[str, ...],
    tables: tuple[str, ...],
    create_revision_table: bool,
) -> None:
    engine = _release_revision_engine(
        tmp_path / f"release-{case}.db",
        revisions,
        tables,
        create_revision_table=create_revision_table,
    )
    try:
        expected = _legacy_release_capability_result(engine)
        actual = manifest.inspect_enterprise_knowledge_base_release_capability(engine)
        assert actual == expected
        if case == "pre-minimum-empty":
            assert actual == ("not_available", ())
        elif case in {"minimum-empty", "later-empty"}:
            assert actual == (
                "unavailable",
                (
                    "required tables are missing: "
                    + ", ".join(sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES)),
                ),
            )
        if case in {
            "pre-minimum-empty",
            "pre-minimum-partial",
            "unknown-empty",
            "unknown-partial",
            "revision-table-absent",
            "revision-table-absent-partial",
            "revision-table-empty",
            "revision-value-empty",
            "multiple-revisions",
        }:
            assert actual == manifest._KNOWLEDGE_SERVING_ORIGINAL_RELEASE_CAPABILITY(engine)
    finally:
        engine.dispose()


def test_release_policy_preserves_legacy_probe_and_fallback_exception_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe(_connection):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(capability_producers, "inspect", fail_probe)
    with pytest.raises(RuntimeError, match="probe failed"):
        manifest.inspect_enterprise_knowledge_base_release_capability(object())
    monkeypatch.setattr(capability_producers, "inspect", sqlalchemy_inspect)

    engine = _release_revision_engine(
        tmp_path / "release-fallback-error.db",
        (),
        create_revision_table=False,
    )
    calls = 0

    def fail_legacy_fallback(_bind, _inspector):
        nonlocal calls
        calls += 1
        raise RuntimeError("fallback failed")

    monkeypatch.setattr(manifest, "_schema_connection", fail_legacy_fallback)
    try:
        assert manifest.inspect_enterprise_knowledge_base_release_capability(engine) == (
            "unavailable",
            ("knowledge base release schema inspection failed: RuntimeError",),
        )
        assert calls == 1
    finally:
        engine.dispose()


def test_release_policy_matches_legacy_behavior_for_a_complete_catalog(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-complete.db').as_posix()}"
    manifest.upgrade_catalog(database_url)
    engine = create_engine(database_url)
    try:
        assert manifest.inspect_enterprise_knowledge_base_release_capability(engine) == (
            "ready",
            (),
        )
        assert manifest.inspect_enterprise_knowledge_base_release_capability(engine) == (
            _legacy_release_capability_result(engine)
        )
    finally:
        engine.dispose()


def test_release_quality_certification_builtin_policy_is_reserved() -> None:
    policy = _policy(issue_checker=lambda _connection: ())
    with pytest.raises(ValueError, match="reserved"):
        manifest.register_catalog_capability_producer("release_quality_certification", policy)
    with pytest.raises(ValueError, match="reserved"):
        manifest.unregister_catalog_capability_producer("release_quality_certification")


def test_release_quality_certification_builtin_registry_rejects_raw_replacement() -> None:
    name = "release_quality_certification"
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory(name)
    registry.register(name, lambda _bind: ("ready", ()), replace=True)
    try:
        with pytest.raises(TypeError, match="built-in.*contract was replaced"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin, replace=True)


def test_release_quality_certification_builtin_registry_rejects_raw_removal() -> None:
    name = "release_quality_certification"
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory(name)
    registry.unregister(name)
    try:
        with pytest.raises(TypeError, match="built-in.*was unregistered"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin)


@pytest.mark.parametrize(
    ("case", "revisions", "tables", "create_revision_table"),
    [
        (
            "pre-minimum-empty",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,),
            (),
            True,
        ),
        (
            "pre-minimum-partial",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,),
            (sorted(manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES)[0],),
            True,
        ),
        (
            "minimum-empty",
            (manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,),
            (),
            True,
        ),
        (
            "later-empty",
            (manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,),
            (),
            True,
        ),
        (
            "minimum-partial",
            (manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,),
            (sorted(manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES)[0],),
            True,
        ),
        (
            "later-partial",
            (manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,),
            (sorted(manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES)[0],),
            True,
        ),
        ("unknown-empty", ("unknown_catalog_revision",), (), True),
        (
            "unknown-partial",
            ("unknown_catalog_revision",),
            (sorted(manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES)[0],),
            True,
        ),
        ("revision-table-absent", (), (), False),
        (
            "revision-table-absent-partial",
            (),
            (sorted(manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES)[0],),
            False,
        ),
        ("revision-table-empty", (), (), True),
        ("revision-value-empty", ("",), (), True),
        (
            "multiple-revisions",
            (
                manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,
                manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
            ),
            (),
            True,
        ),
    ],
)
def test_release_quality_certification_policy_preserves_legacy_revision_matrix(
    tmp_path: Path,
    case: str,
    revisions: tuple[str, ...],
    tables: tuple[str, ...],
    create_revision_table: bool,
) -> None:
    engine = _release_revision_engine(
        tmp_path / f"release-quality-{case}.db",
        revisions,
        tables,
        create_revision_table=create_revision_table,
    )
    try:
        expected = _legacy_release_quality_capability_result(engine)
        actual = manifest.inspect_enterprise_release_quality_certification_capability(engine)
        assert actual == expected
        if case == "pre-minimum-empty":
            assert actual == ("not_available", ())
        elif case in {"minimum-empty", "later-empty"}:
            assert actual == (
                "unavailable",
                (
                    "required tables are missing: "
                    + ", ".join(
                        sorted(manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES)
                    ),
                ),
            )
        if case in {
            "pre-minimum-empty",
            "pre-minimum-partial",
            "unknown-empty",
            "unknown-partial",
            "revision-table-absent",
            "revision-table-absent-partial",
            "revision-table-empty",
            "revision-value-empty",
            "multiple-revisions",
        }:
            assert actual == manifest._KNOWLEDGE_SERVING_ORIGINAL_QUALITY_CAPABILITY(engine)
    finally:
        engine.dispose()


def test_release_quality_certification_policy_preserves_legacy_exception_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe(_connection):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(capability_producers, "inspect", fail_probe)
    with pytest.raises(RuntimeError, match="probe failed"):
        manifest.inspect_enterprise_release_quality_certification_capability(object())
    monkeypatch.setattr(capability_producers, "inspect", sqlalchemy_inspect)

    engine = _release_revision_engine(
        tmp_path / "release-quality-fallback-error.db",
        (),
        create_revision_table=False,
    )
    calls = 0

    def fail_legacy_fallback(_bind, _inspector):
        nonlocal calls
        calls += 1
        raise RuntimeError("fallback failed")

    monkeypatch.setattr(manifest, "_schema_connection", fail_legacy_fallback)
    try:
        assert manifest.inspect_enterprise_release_quality_certification_capability(engine) == (
            "unavailable",
            ("Release quality schema inspection failed: RuntimeError",),
        )
        assert calls == 1
    finally:
        engine.dispose()


def test_release_quality_certification_policy_matches_legacy_complete_catalog(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-quality-complete.db').as_posix()}"
    manifest.upgrade_catalog(database_url)
    engine = create_engine(database_url)
    try:
        actual = manifest.inspect_enterprise_release_quality_certification_capability(engine)
        assert actual == ("ready", ())
        assert actual == _legacy_release_quality_capability_result(engine)
    finally:
        engine.dispose()


def test_release_quality_operations_builtin_policy_is_reserved() -> None:
    policy = _policy(issue_checker=lambda _connection: ())
    with pytest.raises(ValueError, match="reserved"):
        manifest.register_catalog_capability_producer("release_quality_operations", policy)
    with pytest.raises(ValueError, match="reserved"):
        manifest.unregister_catalog_capability_producer("release_quality_operations")


def test_release_quality_operations_builtin_registry_rejects_raw_replacement() -> None:
    name = "release_quality_operations"
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory(name)
    registry.register(name, lambda _bind: ("ready", ()), replace=True)
    try:
        with pytest.raises(TypeError, match="built-in.*contract was replaced"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin, replace=True)


def test_release_quality_operations_builtin_registry_rejects_raw_removal() -> None:
    name = "release_quality_operations"
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory(name)
    registry.unregister(name)
    try:
        with pytest.raises(TypeError, match="built-in.*was unregistered"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin)


@pytest.mark.parametrize(
    ("case", "revisions", "tables", "create_revision_table"),
    [
        (
            "pre-minimum-empty",
            (manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,),
            (),
            True,
        ),
        (
            "pre-minimum-partial",
            (manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,),
            (sorted(manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES)[0],),
            True,
        ),
        (
            "minimum-empty",
            (manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,),
            (),
            True,
        ),
        (
            "later-empty",
            (manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,),
            (),
            True,
        ),
        (
            "minimum-partial",
            (manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,),
            (sorted(manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES)[0],),
            True,
        ),
        (
            "later-partial",
            (manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,),
            (sorted(manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES)[0],),
            True,
        ),
        ("unknown-empty", ("unknown_catalog_revision",), (), True),
        (
            "unknown-partial",
            ("unknown_catalog_revision",),
            (sorted(manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES)[0],),
            True,
        ),
        ("revision-table-absent", (), (), False),
        (
            "revision-table-absent-partial",
            (),
            (sorted(manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES)[0],),
            False,
        ),
        ("revision-table-empty", (), (), True),
        ("revision-value-empty", ("",), (), True),
        (
            "multiple-revisions",
            (
                manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
                manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
            ),
            (),
            True,
        ),
    ],
)
def test_release_quality_operations_policy_preserves_legacy_revision_matrix(
    tmp_path: Path,
    case: str,
    revisions: tuple[str, ...],
    tables: tuple[str, ...],
    create_revision_table: bool,
) -> None:
    engine = _release_revision_engine(
        tmp_path / f"release-quality-operations-{case}.db",
        revisions,
        tables,
        create_revision_table=create_revision_table,
    )
    try:
        expected = _legacy_release_quality_operations_capability_result(engine)
        actual = manifest.inspect_enterprise_release_quality_operations_capability(engine)
        assert actual == expected
        if case == "pre-minimum-empty":
            assert actual == ("not_available", ())
        elif case in {"minimum-empty", "later-empty"}:
            assert actual == (
                "unavailable",
                (
                    "required tables are missing: "
                    + ", ".join(sorted(manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES)),
                ),
            )
        if case in {
            "pre-minimum-empty",
            "pre-minimum-partial",
            "unknown-empty",
            "unknown-partial",
            "revision-table-absent",
            "revision-table-absent-partial",
            "revision-table-empty",
            "revision-value-empty",
            "multiple-revisions",
        }:
            assert actual == manifest._KNOWLEDGE_SERVING_ORIGINAL_QUALITY_OPERATIONS_CAPABILITY(
                engine
            )
    finally:
        engine.dispose()


def test_release_quality_operations_policy_preserves_legacy_exception_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe(_connection):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(capability_producers, "inspect", fail_probe)
    with pytest.raises(RuntimeError, match="probe failed"):
        manifest.inspect_enterprise_release_quality_operations_capability(object())
    monkeypatch.setattr(capability_producers, "inspect", sqlalchemy_inspect)

    engine = _release_revision_engine(
        tmp_path / "release-quality-operations-fallback-error.db",
        (),
        create_revision_table=False,
    )
    calls = 0

    def fail_legacy_fallback(_bind, _inspector):
        nonlocal calls
        calls += 1
        raise RuntimeError("fallback failed")

    monkeypatch.setattr(manifest, "_schema_connection", fail_legacy_fallback)
    try:
        assert manifest.inspect_enterprise_release_quality_operations_capability(engine) == (
            "unavailable",
            ("Release quality operations schema inspection failed: RuntimeError",),
        )
        assert calls == 1
    finally:
        engine.dispose()


def test_release_quality_operations_policy_matches_legacy_complete_catalog(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release-quality-operations-complete.db').as_posix()}"
    manifest.upgrade_catalog(database_url)
    engine = create_engine(database_url)
    try:
        actual = manifest.inspect_enterprise_release_quality_operations_capability(engine)
        assert actual == ("ready", ())
        assert actual == _legacy_release_quality_operations_capability_result(engine)
    finally:
        engine.dispose()


def test_notification_center_builtin_policy_is_reserved() -> None:
    policy = _policy(issue_checker=lambda _connection: ())
    with pytest.raises(ValueError, match="reserved"):
        manifest.register_catalog_capability_producer("notification_center", policy)
    with pytest.raises(ValueError, match="reserved"):
        manifest.unregister_catalog_capability_producer("notification_center")


def test_notification_center_builtin_registry_rejects_raw_replacement() -> None:
    name = "notification_center"
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory(name)
    registry.register(name, lambda _bind: ("ready", ()), replace=True)
    try:
        with pytest.raises(TypeError, match="built-in.*contract was replaced"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin, replace=True)


def test_notification_center_builtin_registry_rejects_raw_removal() -> None:
    name = "notification_center"
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory(name)
    registry.unregister(name)
    try:
        with pytest.raises(TypeError, match="built-in.*was unregistered"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin)


@pytest.mark.parametrize(
    ("case", "revisions", "tables", "create_revision_table"),
    [
        (
            "pre-minimum-empty",
            (manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,),
            (),
            True,
        ),
        (
            "pre-minimum-partial",
            (manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,),
            (sorted(manifest.ENTERPRISE_NOTIFICATION_CENTER_TABLES)[0],),
            True,
        ),
        (
            "minimum-empty",
            (manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,),
            (),
            True,
        ),
        (
            "later-empty",
            (manifest.ENTERPRISE_CONTENT_RECOVERY_REVISION,),
            (),
            True,
        ),
        (
            "minimum-partial",
            (manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,),
            (sorted(manifest.ENTERPRISE_NOTIFICATION_CENTER_TABLES)[0],),
            True,
        ),
        (
            "later-partial",
            (manifest.ENTERPRISE_CONTENT_RECOVERY_REVISION,),
            (sorted(manifest.ENTERPRISE_NOTIFICATION_CENTER_TABLES)[0],),
            True,
        ),
        ("unknown-empty", ("unknown_catalog_revision",), (), True),
        (
            "unknown-partial",
            ("unknown_catalog_revision",),
            (sorted(manifest.ENTERPRISE_NOTIFICATION_CENTER_TABLES)[0],),
            True,
        ),
        ("revision-table-absent", (), (), False),
        (
            "revision-table-absent-partial",
            (),
            (sorted(manifest.ENTERPRISE_NOTIFICATION_CENTER_TABLES)[0],),
            False,
        ),
        ("revision-table-empty", (), (), True),
        ("revision-value-empty", ("",), (), True),
        (
            "multiple-revisions",
            (
                manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
                manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,
            ),
            (),
            True,
        ),
    ],
)
def test_notification_center_policy_preserves_legacy_revision_matrix(
    tmp_path: Path,
    case: str,
    revisions: tuple[str, ...],
    tables: tuple[str, ...],
    create_revision_table: bool,
) -> None:
    engine = _release_revision_engine(
        tmp_path / f"notification-center-{case}.db",
        revisions,
        tables,
        create_revision_table=create_revision_table,
    )
    try:
        expected = _legacy_notification_capability_result(engine)
        actual = manifest.inspect_enterprise_notification_center_capability(engine)
        assert actual == expected
        if case == "pre-minimum-empty":
            assert actual == ("not_available", ())
        elif case in {"minimum-empty", "later-empty"}:
            assert actual == (
                "unavailable",
                (
                    "required tables are missing: "
                    + ", ".join(sorted(manifest.ENTERPRISE_NOTIFICATION_CENTER_TABLES)),
                ),
            )
        if case in {
            "pre-minimum-empty",
            "pre-minimum-partial",
            "unknown-empty",
            "unknown-partial",
            "revision-table-absent",
            "revision-table-absent-partial",
            "revision-table-empty",
            "revision-value-empty",
            "multiple-revisions",
        }:
            assert actual == manifest._KNOWLEDGE_SERVING_ORIGINAL_NOTIFICATION_CAPABILITY(
                engine
            )
    finally:
        engine.dispose()


def test_notification_center_policy_preserves_legacy_exception_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe(_connection):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(capability_producers, "inspect", fail_probe)
    with pytest.raises(RuntimeError, match="probe failed"):
        manifest.inspect_enterprise_notification_center_capability(object())
    monkeypatch.setattr(capability_producers, "inspect", sqlalchemy_inspect)

    engine = _release_revision_engine(
        tmp_path / "notification-center-fallback-error.db",
        (),
        create_revision_table=False,
    )
    calls = 0

    def fail_legacy_fallback(_bind, _inspector):
        nonlocal calls
        calls += 1
        raise RuntimeError("fallback failed")

    monkeypatch.setattr(manifest, "_schema_connection", fail_legacy_fallback)
    try:
        assert manifest.inspect_enterprise_notification_center_capability(engine) == (
            "unavailable",
            ("Notification Center schema inspection failed: RuntimeError",),
        )
        assert calls == 1
    finally:
        engine.dispose()


def test_notification_center_policy_matches_legacy_complete_catalog(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'notification-center-complete.db').as_posix()}"
    manifest.upgrade_catalog(database_url)
    engine = create_engine(database_url)
    try:
        actual = manifest.inspect_enterprise_notification_center_capability(engine)
        assert actual == ("ready", ())
        assert actual == _legacy_notification_capability_result(engine)
    finally:
        engine.dispose()


def test_registry_builtin_policy_is_reserved() -> None:
    policy = _policy(issue_checker=lambda _connection: ())
    with pytest.raises(ValueError, match="reserved"):
        manifest.register_catalog_capability_producer("knowledge_base_registry", policy)
    with pytest.raises(ValueError, match="reserved"):
        manifest.unregister_catalog_capability_producer("knowledge_base_registry")


def test_registry_builtin_registry_rejects_raw_replacement() -> None:
    name = "knowledge_base_registry"
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory(name)
    registry.register(name, lambda _bind: ("ready", ()), replace=True)
    try:
        with pytest.raises(TypeError, match="built-in.*contract was replaced"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin, replace=True)


def test_registry_builtin_registry_rejects_raw_removal() -> None:
    name = "knowledge_base_registry"
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory(name)
    registry.unregister(name)
    try:
        with pytest.raises(TypeError, match="built-in.*was unregistered"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin)


@pytest.mark.parametrize(
    ("case", "revisions", "tables", "create_revision_table"),
    [
        (
            "pre-minimum-empty",
            (manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,),
            (),
            True,
        ),
        (
            "pre-minimum-partial",
            (manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,),
            (sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES)[0],),
            True,
        ),
        (
            "minimum-empty",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,),
            (),
            True,
        ),
        (
            "later-empty",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,),
            (),
            True,
        ),
        (
            "minimum-partial",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,),
            (sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES)[0],),
            True,
        ),
        (
            "later-partial",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,),
            (sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES)[0],),
            True,
        ),
        ("unknown-empty", ("unknown_catalog_revision",), (), True),
        (
            "unknown-partial",
            ("unknown_catalog_revision",),
            (sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES)[0],),
            True,
        ),
        ("revision-table-absent", (), (), False),
        (
            "revision-table-absent-partial",
            (),
            (sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES)[0],),
            False,
        ),
        ("revision-table-empty", (), (), True),
        ("revision-value-empty", ("",), (), True),
        (
            "multiple-revisions",
            (
                manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,
                manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,
            ),
            (),
            True,
        ),
    ],
)
def test_registry_policy_preserves_legacy_revision_matrix(
    tmp_path: Path,
    case: str,
    revisions: tuple[str, ...],
    tables: tuple[str, ...],
    create_revision_table: bool,
) -> None:
    engine = _release_revision_engine(
        tmp_path / f"knowledge-base-registry-{case}.db",
        revisions,
        tables,
        create_revision_table=create_revision_table,
    )
    try:
        expected = _legacy_registry_capability_result(engine)
        actual = manifest.inspect_enterprise_knowledge_base_registry_capability(engine)
        assert actual == expected
        if case == "pre-minimum-empty":
            assert actual == ("not_available", ())
        elif case in {"minimum-empty", "later-empty"}:
            assert actual == (
                "unavailable",
                (
                    "required tables are missing: "
                    + ", ".join(sorted(manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES)),
                ),
            )
        if case in {
            "pre-minimum-empty",
            "pre-minimum-partial",
            "unknown-empty",
            "unknown-partial",
            "revision-table-absent",
            "revision-table-absent-partial",
            "revision-table-empty",
            "revision-value-empty",
            "multiple-revisions",
        }:
            assert actual == manifest._KNOWLEDGE_SERVING_ORIGINAL_REGISTRY_CAPABILITY(engine)
    finally:
        engine.dispose()


def test_registry_policy_preserves_legacy_exception_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe(_connection):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(capability_producers, "inspect", fail_probe)
    with pytest.raises(RuntimeError, match="probe failed"):
        manifest.inspect_enterprise_knowledge_base_registry_capability(object())
    monkeypatch.setattr(capability_producers, "inspect", sqlalchemy_inspect)

    engine = _release_revision_engine(
        tmp_path / "knowledge-base-registry-fallback-error.db",
        (),
        create_revision_table=False,
    )
    calls = 0

    def fail_legacy_fallback(_bind, _inspector):
        nonlocal calls
        calls += 1
        raise RuntimeError("fallback failed")

    monkeypatch.setattr(manifest, "_schema_connection", fail_legacy_fallback)
    try:
        assert manifest.inspect_enterprise_knowledge_base_registry_capability(engine) == (
            "unavailable",
            ("knowledge base registry schema inspection failed: RuntimeError",),
        )
        assert calls == 1
    finally:
        engine.dispose()


def test_registry_policy_matches_legacy_complete_catalog(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'knowledge-base-registry-complete.db').as_posix()}"
    manifest.upgrade_catalog(database_url)
    engine = create_engine(database_url)
    try:
        actual = manifest.inspect_enterprise_knowledge_base_registry_capability(engine)
        assert actual == ("ready", ())
        assert actual == _legacy_registry_capability_result(engine)
    finally:
        engine.dispose()


def test_workspace_authorization_builtin_policy_is_reserved() -> None:
    policy = _policy(issue_checker=lambda _connection: ())
    with pytest.raises(ValueError, match="reserved"):
        manifest.register_catalog_capability_producer("workspace_authorization", policy)
    with pytest.raises(ValueError, match="reserved"):
        manifest.unregister_catalog_capability_producer("workspace_authorization")


def test_workspace_authorization_builtin_registry_rejects_raw_replacement() -> None:
    name = "workspace_authorization"
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory(name)
    registry.register(name, lambda _bind: ("ready", ()), replace=True)
    try:
        with pytest.raises(TypeError, match="built-in.*contract was replaced"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin, replace=True)


def test_workspace_authorization_builtin_registry_rejects_raw_removal() -> None:
    name = "workspace_authorization"
    registry = manifest._CATALOG_CAPABILITY_PRODUCERS
    builtin = registry.get_factory(name)
    registry.unregister(name)
    try:
        with pytest.raises(TypeError, match="built-in.*was unregistered"):
            manifest.inspect_catalog_capability(name, object())
    finally:
        registry.register(name, builtin)


@pytest.mark.parametrize(
    ("case", "revisions", "tables", "create_revision_table"),
    [
        (
            "pre-minimum-empty",
            (manifest.ENTERPRISE_WORKSPACE_CONTROL_REVISION,),
            (),
            True,
        ),
        (
            "pre-minimum-partial",
            (manifest.ENTERPRISE_WORKSPACE_CONTROL_REVISION,),
            (sorted(manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES)[0],),
            True,
        ),
        (
            "minimum-empty",
            (manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,),
            (),
            True,
        ),
        (
            "later-empty",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,),
            (),
            True,
        ),
        (
            "minimum-partial",
            (manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,),
            (sorted(manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES)[0],),
            True,
        ),
        (
            "later-partial",
            (manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,),
            (sorted(manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES)[0],),
            True,
        ),
        ("unknown-empty", ("unknown_catalog_revision",), (), True),
        (
            "unknown-partial",
            ("unknown_catalog_revision",),
            (sorted(manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES)[0],),
            True,
        ),
        ("revision-table-absent", (), (), False),
        (
            "revision-table-absent-partial",
            (),
            (sorted(manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES)[0],),
            False,
        ),
        ("revision-table-empty", (), (), True),
        ("revision-value-empty", ("",), (), True),
        (
            "multiple-revisions",
            (
                manifest.ENTERPRISE_WORKSPACE_CONTROL_REVISION,
                manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,
            ),
            (),
            True,
        ),
    ],
)
def test_workspace_authorization_policy_preserves_legacy_revision_matrix(
    tmp_path: Path,
    case: str,
    revisions: tuple[str, ...],
    tables: tuple[str, ...],
    create_revision_table: bool,
) -> None:
    engine = _release_revision_engine(
        tmp_path / f"workspace-authorization-{case}.db",
        revisions,
        tables,
        create_revision_table=create_revision_table,
    )
    try:
        expected = _legacy_workspace_authorization_capability_result(engine)
        actual = manifest.inspect_workspace_authorization_capability(engine)
        assert actual == expected
        if case == "pre-minimum-empty":
            assert actual == ("not_available", ())
        elif case in {"minimum-empty", "later-empty"}:
            assert actual == (
                "unavailable",
                (
                    "required tables are missing: "
                    + ", ".join(sorted(manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES)),
                ),
            )
        if case in {
            "pre-minimum-empty",
            "pre-minimum-partial",
            "unknown-empty",
            "unknown-partial",
            "revision-table-absent",
            "revision-table-absent-partial",
            "revision-table-empty",
            "revision-value-empty",
            "multiple-revisions",
        }:
            assert actual == manifest._KNOWLEDGE_SERVING_ORIGINAL_WORKSPACE_AUTHORIZATION_CAPABILITY(
                engine
            )
    finally:
        engine.dispose()


def test_workspace_authorization_policy_preserves_unstamped_empty_and_nonempty_semantics(
    tmp_path: Path,
) -> None:
    empty_engine = _release_revision_engine(
        tmp_path / "workspace-authorization-unstamped-empty.db",
        (),
        (sorted(manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES)[0],),
        create_revision_table=False,
    )
    nonempty_engine = _release_revision_engine(
        tmp_path / "workspace-authorization-unstamped-nonempty.db",
        (),
        (sorted(manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES)[0],),
        create_revision_table=False,
    )
    try:
        with nonempty_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_workspace_authorization_policies (id) "
                    "VALUES (1)"
                )
            )
        assert manifest.inspect_workspace_authorization_capability(empty_engine) == (
            "not_available",
            (),
        )
        assert manifest.inspect_workspace_authorization_capability(nonempty_engine) == (
            "unavailable",
            (
                "catalog is not at a known pre-0027, 0027, 0028, 0029, 0030 or 0031 revision",
            ),
        )
    finally:
        empty_engine.dispose()
        nonempty_engine.dispose()


def test_workspace_authorization_policy_preserves_legacy_exception_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe(_connection):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(capability_producers, "inspect", fail_probe)
    with pytest.raises(RuntimeError, match="probe failed"):
        manifest.inspect_workspace_authorization_capability(object())
    monkeypatch.setattr(capability_producers, "inspect", sqlalchemy_inspect)

    engine = _release_revision_engine(
        tmp_path / "workspace-authorization-fallback-error.db",
        (),
        create_revision_table=False,
    )
    calls = 0

    def fail_legacy_fallback(_bind, _inspector):
        nonlocal calls
        calls += 1
        raise RuntimeError("fallback failed")

    monkeypatch.setattr(manifest, "_schema_connection", fail_legacy_fallback)
    try:
        assert manifest.inspect_workspace_authorization_capability(engine) == (
            "unavailable",
            ("workspace authorization schema inspection failed: RuntimeError",),
        )
        assert calls == 1
    finally:
        engine.dispose()


def test_workspace_authorization_policy_matches_legacy_complete_catalog(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'workspace-authorization-complete.db').as_posix()}"
    manifest.upgrade_catalog(database_url)
    engine = create_engine(database_url)
    try:
        actual = manifest.inspect_workspace_authorization_capability(engine)
        assert actual == ("ready", ())
        assert actual == _legacy_workspace_authorization_capability_result(engine)
    finally:
        engine.dispose()


def test_content_recovery_policy_preserves_pre_0033_not_available_boundary(
    tmp_path: Path,
) -> None:
    legacy = _content_recovery_revision_engine(
        tmp_path / "content-recovery-legacy.db",
        (manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,),
    )
    partial = _content_recovery_revision_engine(
        tmp_path / "content-recovery-legacy-partial.db",
        (manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,),
        tables=(sorted(manifest.ENTERPRISE_CONTENT_RECOVERY_TABLES)[0],),
    )
    try:
        assert manifest.inspect_enterprise_content_recovery_capability(legacy) == (
            "not_available",
            (),
        )
        state, issues = manifest.inspect_enterprise_content_recovery_capability(partial)
        assert state == "unavailable"
        assert issues == ("catalog is not at a known pre-0033, 0033 or 0034 revision",)
    finally:
        legacy.dispose()
        partial.dispose()


def test_content_recovery_registry_delegates_historical_branches_to_frozen_inspector(
    tmp_path: Path,
) -> None:
    engines = [
        _content_recovery_revision_engine(
            tmp_path / "content-recovery-legacy-fallback.db",
            (manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,),
        ),
        _content_recovery_revision_engine(
            tmp_path / "content-recovery-partial-fallback.db",
            (manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,),
            tables=(sorted(manifest.ENTERPRISE_CONTENT_RECOVERY_TABLES)[0],),
        ),
        _content_recovery_revision_engine(
            tmp_path / "content-recovery-unknown-fallback.db",
            ("unknown_catalog_revision",),
        ),
        _content_recovery_revision_engine(
            tmp_path / "content-recovery-multiple-fallback.db",
            (
                manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,
                manifest.ENTERPRISE_CONTENT_RECOVERY_REVISION,
            ),
        ),
    ]
    missing_revision = create_engine(
        f"sqlite:///{(tmp_path / 'content-recovery-missing-revision-fallback.db').as_posix()}"
    )
    engines.append(missing_revision)
    try:
        for engine in engines:
            assert manifest.inspect_enterprise_content_recovery_capability(engine) == (
                _legacy_content_recovery_capability_result(engine)
            )
    finally:
        for engine in engines:
            engine.dispose()


def test_automation_registry_delegates_historical_branches_to_frozen_inspector(
    tmp_path: Path,
) -> None:
    automation_table = sorted(manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES)[0]
    engines = [
        _release_revision_engine(
            tmp_path / "automation-pre-minimum.db",
            (manifest.ENTERPRISE_CONTENT_RECOVERY_REVISION,),
        ),
        _release_revision_engine(
            tmp_path / "automation-pre-minimum-partial.db",
            (manifest.ENTERPRISE_CONTENT_RECOVERY_REVISION,),
            tables=(automation_table,),
        ),
        _release_revision_engine(
            tmp_path / "automation-exact-minimum-missing.db",
            (manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,),
        ),
        _release_revision_engine(
            tmp_path / "automation-unknown.db",
            ("unknown_catalog_revision",),
        ),
        _release_revision_engine(
            tmp_path / "automation-multiple.db",
            (
                manifest.ENTERPRISE_CONTENT_RECOVERY_REVISION,
                manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
            ),
        ),
    ]
    missing_revision = _release_revision_engine(
        tmp_path / "automation-missing-revision.db",
        (),
        create_revision_table=False,
    )
    engines.append(missing_revision)
    try:
        for engine in engines:
            assert manifest.inspect_enterprise_automation_workflows_capability(engine) == (
                _legacy_automation_capability_result(engine)
            )
    finally:
        for engine in engines:
            engine.dispose()


def test_content_recovery_policy_keeps_post_minimum_missing_tables_unavailable(
    tmp_path: Path,
) -> None:
    engine = _content_recovery_revision_engine(
        tmp_path / "content-recovery-post-minimum.db",
        (manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REVISION,),
    )
    try:
        state, issues = manifest.inspect_enterprise_content_recovery_capability(engine)
        assert state == "unavailable"
        assert any(issue.startswith("missing table ") for issue in issues)
    finally:
        engine.dispose()


def test_content_recovery_policy_preserves_multiple_unknown_and_missing_revision_states(
    tmp_path: Path,
) -> None:
    multiple = _content_recovery_revision_engine(
        tmp_path / "content-recovery-multiple.db",
        (
            manifest.ENTERPRISE_NOTIFICATION_CENTER_REVISION,
            manifest.ENTERPRISE_CONTENT_RECOVERY_REVISION,
        ),
    )
    unknown = _content_recovery_revision_engine(
        tmp_path / "content-recovery-unknown.db",
        ("unknown_catalog_revision",),
    )
    missing = create_engine(
        f"sqlite:///{(tmp_path / 'content-recovery-no-revision.db').as_posix()}"
    )
    try:
        assert manifest.inspect_enterprise_content_recovery_capability(multiple) == (
            "unavailable",
            ("alembic_version contains multiple revisions",),
        )
        assert manifest.inspect_enterprise_content_recovery_capability(unknown) == (
            "unavailable",
            ("catalog is not at a known pre-0033, 0033 or 0034 revision",),
        )
        assert manifest.inspect_enterprise_content_recovery_capability(missing) == (
            "unavailable",
            ("catalog is not at a known pre-0033, 0033 or 0034 revision",),
        )
    finally:
        multiple.dispose()
        unknown.dispose()
        missing.dispose()


def test_capability_policy_rejects_invalid_checkers_at_registration() -> None:
    with pytest.raises(ValueError, match="issue_checker"):
        manifest.register_catalog_capability_producer(
            "invalid_probe_test",
            _policy(issue_checker=lambda: ()),
        )


def test_capability_policy_rejects_unknown_minimum_revision_at_registration() -> None:
    with pytest.raises(ValueError, match="known catalog revision order"):
        manifest.register_catalog_capability_producer(
            "unknown_revision_probe_test",
            _policy(
                issue_checker=lambda _connection: (),
                minimum_revision="misspelled_revision",
            ),
        )


def test_capability_policy_rejects_async_checkers_at_registration() -> None:
    async def issue_checker(_connection):
        return ()

    with pytest.raises(ValueError, match="must be synchronous"):
        manifest.register_catalog_capability_producer(
            "async_probe_test",
            _policy(issue_checker=issue_checker),
        )


def test_builtin_capability_producers_cannot_be_replaced() -> None:
    with pytest.raises(ValueError, match="reserved"):
        manifest.register_catalog_capability_producer(
            "knowledge_serving_reliability",
            _policy(issue_checker=lambda _connection: ()),
        )
    with pytest.raises(ValueError, match="reserved"):
        manifest.unregister_catalog_capability_producer("knowledge_serving_reliability")
    with pytest.raises(ValueError, match="reserved"):
        manifest.register_catalog_capability_producer(
            "knowledge_operations_feedback",
            _policy(issue_checker=lambda _connection: ()),
        )
    with pytest.raises(ValueError, match="reserved"):
        manifest.unregister_catalog_capability_producer("knowledge_operations_feedback")
    with pytest.raises(ValueError, match="reserved"):
        manifest.register_catalog_capability_producer(
            "automation_workflows",
            _policy(issue_checker=lambda _connection: ()),
        )
    with pytest.raises(ValueError, match="reserved"):
        manifest.unregister_catalog_capability_producer("automation_workflows")
    with pytest.raises(ValueError, match="reserved"):
        manifest.register_catalog_capability_producer(
            "content_recovery",
            _policy(issue_checker=lambda _connection: ()),
        )
    with pytest.raises(ValueError, match="reserved"):
        manifest.unregister_catalog_capability_producer("content_recovery")
    with pytest.raises(ValueError, match="reserved"):
        manifest.register_catalog_capability_producer(
            "task_operations",
            _policy(issue_checker=lambda _connection: ()),
        )
    with pytest.raises(ValueError, match="reserved"):
        manifest.unregister_catalog_capability_producer("task_operations")


@pytest.mark.parametrize(
    ("inspector", "capability_tables"),
    [
        (
            manifest.inspect_enterprise_knowledge_serving_reliability_capability,
            manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES,
        ),
        (
            manifest.inspect_enterprise_knowledge_operations_feedback_capability,
            manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_TABLES,
        ),
    ],
)
def test_known_legacy_revision_without_capability_tables_remains_not_available(
    tmp_path: Path,
    inspector,
    capability_tables: frozenset[str],
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) VALUES "
                f"('{manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION}')"
            )
        )
    try:
        assert not (capability_tables & set(sqlalchemy_inspect(engine).get_table_names()))
        assert inspector(engine) == ("not_available", ())
    finally:
        engine.dispose()


def test_known_legacy_revision_with_partial_capability_tables_fails_closed(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy-partial.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) VALUES "
                f"('{manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION}')"
            )
        )
        connection.execute(
            text("CREATE TABLE tenant_knowledge_serving_profiles (id VARCHAR(64) PRIMARY KEY)")
        )
    try:
        state, issues = manifest.inspect_enterprise_knowledge_serving_reliability_capability(engine)
        assert state == "unavailable"
        assert any("known pre-0036 or 0036 revision" in issue for issue in issues)
    finally:
        engine.dispose()
