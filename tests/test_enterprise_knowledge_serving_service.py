from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import Boolean, DateTime, Integer, JSON, MetaData, String, Table, func, select
from sqlalchemy.orm import Session

from models.orm import Account, Tenant, TenantMember

MODULE = "core.enterprise_knowledge_serving_service"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
DIGEST = "a" * 64


def service() -> Any:
    import importlib

    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        raise AssertionError(f"Stage26 service module is missing: {exc}") from exc


def operation(name: str) -> Callable[..., Any]:
    value = getattr(service(), name, None)
    assert callable(value), f"Stage26 service operation is missing: {name}"
    return value


def body(value: Any) -> dict[str, Any]:
    projected = getattr(value, "body", value)
    assert isinstance(projected, dict)
    return projected


def _stage_tables() -> tuple[MetaData, dict[str, Table]]:
    metadata = MetaData()

    def common() -> list[Any]:
        return [
            ColumnSpec("id", String(64), primary_key=True),
            ColumnSpec("tenant_id", String(64), nullable=False),
        ]

    tables: dict[str, Table] = {}
    tables["tenant_knowledge_serving_profiles"] = Table(
        "tenant_knowledge_serving_profiles",
        metadata,
        *common(),
        ColumnSpec("workspace_id", String(128), nullable=True),
        ColumnSpec("dataset_id", String(64), nullable=False),
        ColumnSpec("name", String(128), nullable=False),
        ColumnSpec("normalized_name", String(128), nullable=False),
        ColumnSpec("status", String(16), nullable=False),
        ColumnSpec("active_profile_key", String(128), nullable=True),
        ColumnSpec("revision", Integer, nullable=False),
        ColumnSpec("current_policy_revision_id", String(64), nullable=True),
        ColumnSpec("current_snapshot_id", String(64), nullable=True),
        ColumnSpec("created_at", DateTime, nullable=False),
        ColumnSpec("created_by", String(64), nullable=False),
        ColumnSpec("updated_at", DateTime, nullable=False),
        ColumnSpec("updated_by", String(64), nullable=False),
        ColumnSpec("archived_at", DateTime, nullable=True),
        ColumnSpec("archived_by", String(64), nullable=True),
    )
    tables["tenant_knowledge_serving_policy_revisions"] = Table(
        "tenant_knowledge_serving_policy_revisions",
        metadata,
        *common(),
        ColumnSpec("profile_id", String(64), nullable=False),
        ColumnSpec("revision", Integer, nullable=False),
        ColumnSpec("max_source_staleness_seconds", Integer, nullable=False),
        ColumnSpec("max_parse_lag_seconds", Integer, nullable=False),
        ColumnSpec("max_index_lag_seconds", Integer, nullable=False),
        ColumnSpec("max_failed_document_count", Integer, nullable=False),
        ColumnSpec("max_pending_index_count", Integer, nullable=False),
        ColumnSpec("require_current_release", Boolean, nullable=False),
        ColumnSpec("require_passing_certification", Boolean, nullable=False),
        ColumnSpec("policy_digest", String(64), nullable=False),
        ColumnSpec("created_at", DateTime, nullable=False),
        ColumnSpec("created_by", String(64), nullable=False),
    )
    tables["tenant_knowledge_serving_snapshots"] = Table(
        "tenant_knowledge_serving_snapshots",
        metadata,
        *common(),
        ColumnSpec("profile_id", String(64), nullable=False),
        ColumnSpec("policy_revision_id", String(64), nullable=False),
        ColumnSpec("observation_key", String(192), nullable=False),
        ColumnSpec("state", String(16), nullable=False),
        ColumnSpec("source_count", Integer, nullable=False),
        ColumnSpec("ready_source_count", Integer, nullable=False),
        ColumnSpec("stale_source_count", Integer, nullable=False),
        ColumnSpec("active_document_count", Integer, nullable=False),
        ColumnSpec("failed_document_count", Integer, nullable=False),
        ColumnSpec("pending_index_count", Integer, nullable=False),
        ColumnSpec("expected_serving_generation", Integer, nullable=False),
        ColumnSpec("observed_serving_generation", Integer, nullable=False),
        ColumnSpec("current_release_id", String(64), nullable=True),
        ColumnSpec("current_certification_id", String(64), nullable=True),
        ColumnSpec("stage_count", Integer, nullable=False),
        ColumnSpec("ready_stage_count", Integer, nullable=False),
        ColumnSpec("blocked_stage_count", Integer, nullable=False),
        ColumnSpec("snapshot_digest", String(64), nullable=False),
        ColumnSpec("as_of", DateTime, nullable=False),
        ColumnSpec("created_at", DateTime, nullable=False),
        ColumnSpec("created_by", String(64), nullable=False),
    )
    tables["tenant_knowledge_serving_stage_facts"] = Table(
        "tenant_knowledge_serving_stage_facts",
        metadata,
        *common(),
        ColumnSpec("profile_id", String(64), nullable=False),
        ColumnSpec("snapshot_id", String(64), nullable=False),
        ColumnSpec("stage_code", String(16), nullable=False),
        ColumnSpec("sequence", Integer, nullable=False),
        ColumnSpec("state", String(16), nullable=False),
        ColumnSpec("item_count", Integer, nullable=False),
        ColumnSpec("ready_count", Integer, nullable=False),
        ColumnSpec("warning_count", Integer, nullable=False),
        ColumnSpec("pending_count", Integer, nullable=False),
        ColumnSpec("error_count", Integer, nullable=False),
        ColumnSpec("lag_seconds", Integer, nullable=False),
        ColumnSpec("expected_revision", Integer, nullable=True),
        ColumnSpec("observed_revision", Integer, nullable=True),
        ColumnSpec("expected_digest", String(64), nullable=True),
        ColumnSpec("observed_digest", String(64), nullable=True),
        ColumnSpec("safe_error_code", String(128), nullable=True),
        ColumnSpec("safe_error", String(512), nullable=True),
        ColumnSpec("stage_digest", String(64), nullable=False),
        ColumnSpec("observed_at", DateTime, nullable=False),
    )
    tables["tenant_knowledge_serving_evidence_links"] = Table(
        "tenant_knowledge_serving_evidence_links",
        metadata,
        *common(),
        ColumnSpec("profile_id", String(64), nullable=False),
        ColumnSpec("snapshot_id", String(64), nullable=False),
        ColumnSpec("stage_fact_id", String(64), nullable=False),
        ColumnSpec("evidence_kind", String(32), nullable=False),
        ColumnSpec("resource_id", String(128), nullable=False),
        ColumnSpec("resource_revision", Integer, nullable=True),
        ColumnSpec("resource_digest", String(64), nullable=True),
        ColumnSpec("route_code", String(64), nullable=False),
        ColumnSpec("safe_label", String(256), nullable=False),
        ColumnSpec("evidence_digest", String(64), nullable=False),
        ColumnSpec("created_at", DateTime, nullable=False),
    )
    tables["tenant_knowledge_serving_events"] = Table(
        "tenant_knowledge_serving_events",
        metadata,
        *common(),
        ColumnSpec("profile_id", String(64), nullable=False),
        ColumnSpec("snapshot_id", String(64), nullable=True),
        ColumnSpec("stream_key", String(128), nullable=False),
        ColumnSpec("sequence", Integer, nullable=False),
        ColumnSpec("event_type", String(64), nullable=False),
        ColumnSpec("previous_event_digest", String(64), nullable=True),
        ColumnSpec("event_digest", String(64), nullable=False),
        ColumnSpec("actor_id", String(64), nullable=False),
        ColumnSpec("request_id", String(128), nullable=False),
        ColumnSpec("safe_snapshot_json", JSON, nullable=False),
        ColumnSpec("occurred_at", DateTime, nullable=False),
    )
    return metadata, tables


class ColumnSpec:
    def __new__(cls, name: str, type_: Any, **kwargs: Any) -> Any:
        from sqlalchemy import Column

        return Column(name, type_, **kwargs)


def _engine(tmp_path: Path) -> tuple[Any, dict[str, Table]]:
    from test_enterprise_knowledge_base_release_snapshot import _release_engine

    engine, _ = _release_engine(tmp_path)
    metadata, tables = _stage_tables()
    metadata.create_all(engine)
    return engine, tables


def _install_adapters(monkeypatch: pytest.MonkeyPatch, *, state: str = "ready") -> None:
    target = service()

    def make_adapter(code: str) -> Callable[..., dict[str, Any]]:
        def adapter(
            *, session: Any, tenant_id: str, dataset_id: str, policy: dict[str, Any], now: Any
        ) -> dict[str, Any]:
            del session, policy, now
            return {
                "state": state,
                "item_count": 2,
                "ready_count": 2 if code == "source" else 0,
                "warning_count": 0 if state == "ready" else (1 if code in {"source", "parse"} else 0),
                "pending_count": 0 if state == "ready" else (1 if code == "index" else 0),
                "error_count": 0 if state == "ready" else 1,
                "lag_seconds": 0 if state == "ready" else 99,
                "expected_revision": 3,
                "observed_revision": 3,
                "expected_digest": DIGEST,
                "observed_digest": DIGEST,
                "safe_error_code": None if state == "ready" else "stage_lagging",
                "safe_error": None if state == "ready" else f"{code} is lagging",
                "evidence": [
                    {
                        "evidence_kind": "document" if code in {"parse", "chunk"} else "source",
                        "resource_id": f"{code}-resource",
                        "resource_revision": 3,
                        "resource_digest": DIGEST,
                        "route_code": "knowledge_documents"
                        if code in {"parse", "chunk"}
                        else "knowledge_sources",
                        "safe_label": f"{code} evidence",
                    }
                ],
                "tenant_id": tenant_id,
                "dataset_id": dataset_id,
            }

        return adapter

    monkeypatch.setattr(
        target,
        "SERVING_ADAPTER_REGISTRY",
        {code: make_adapter(code) for code in target.SERVING_STAGE_CODES},
    )


def _seed_tenant_b(engine: Any) -> None:
    now = datetime(2026, 8, 30, 12, 0, 0)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-b", name="Tenant B", plan="enterprise", status="active"))
        session.add(Account(id="owner-b", name="Owner B", email="owner-b@stage26.test"))
        session.flush()
        session.add(
            TenantMember(
                account_id="owner-b",
                tenant_id="tenant-b",
                role="owner",
                status="active",
                revision=1,
                created_at=now,
                updated_at=now,
                updated_by="owner-b",
            )
        )
        session.commit()


def _profile(engine: Any) -> dict[str, Any]:
    return body(
        operation("create_serving_profile")(  # type: ignore[misc]
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            workspace_id="workspace-a",
            name="Operations Serving",
            reason="create the serving reliability profile",
            idempotency_key="profile-create-1",
            now=NOW,
        )
    )


def _policy(engine: Any) -> dict[str, Any]:
    _profile(engine)
    return body(
        operation("create_serving_policy_revision")(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            expected_profile_revision=1,
            expected_policy_digest=None,
            max_source_staleness_seconds=3600,
            max_parse_lag_seconds=1800,
            max_index_lag_seconds=1800,
            max_failed_document_count=0,
            max_pending_index_count=0,
            require_current_release=True,
            require_passing_certification=True,
            reason="define bounded serving policy",
            idempotency_key="policy-create-1",
            now=NOW,
        )
    )


def test_stage26_service_module_exposes_explicit_five_adapter_registry() -> None:
    target = service()
    assert tuple(target.SERVING_STAGE_CODES) == ("source", "parse", "chunk", "index", "serve")
    assert tuple(target.SERVING_ADAPTER_ORDER) == tuple(target.SERVING_STAGE_CODES)
    assert tuple(target.SERVING_ADAPTER_REGISTRY) == tuple(target.SERVING_STAGE_CODES)
    assert all(
        callable(target.SERVING_ADAPTER_REGISTRY[code]) for code in target.SERVING_STAGE_CODES
    )


def test_profile_and_policy_mutations_are_idempotent_and_revision_fenced(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    try:
        first = _profile(engine)
        replay = _profile(engine)
        assert first == replay
        assert first["state"] == "applied"
        assert first["profile"]["tenant_id"] == "tenant-a"

        created_policy = _policy(engine)
        assert created_policy["policy"]["revision"] == 1
        assert len(created_policy["policy"]["policy_digest"]) == 64
        with pytest.raises(Exception, match="revision|fence|conflict"):
            operation("create_serving_policy_revision")(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                dataset_id="dataset-a",
                expected_profile_revision=1,
                expected_policy_digest=None,
                max_source_staleness_seconds=3600,
                max_parse_lag_seconds=1800,
                max_index_lag_seconds=1800,
                max_failed_document_count=0,
                max_pending_index_count=0,
                require_current_release=True,
                require_passing_certification=True,
                reason="stale policy fence",
                idempotency_key="policy-create-stale",
                now=NOW,
            )
    finally:
        engine.dispose()


def test_snapshot_recording_is_internal_replay_safe_and_does_not_mutate_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, tables = _engine(tmp_path)
    _install_adapters(monkeypatch)
    try:
        created = _policy(engine)
        profile_id = created["profile"]["id"]
        policy_id = created["policy"]["id"]
        before = body(
            operation("get_serving_summary")(engine, tenant_id="tenant-a", dataset_id="dataset-a")
        )
        result = body(
            operation("record_knowledge_serving_snapshot")(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                dataset_id="dataset-a",
                profile_id=profile_id,
                policy_revision_id=policy_id,
                expected_profile_revision=2,
                reason="record a bounded reliability observation",
                idempotency_key="snapshot-record-1",
                request_id="request-stage26-record-1",
                now=NOW,
            )
        )
        replay = body(
            operation("record_knowledge_serving_snapshot")(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                dataset_id="dataset-a",
                profile_id=profile_id,
                policy_revision_id=policy_id,
                expected_profile_revision=2,
                reason="record a bounded reliability observation",
                idempotency_key="snapshot-record-1",
                request_id="request-stage26-record-1",
                now=NOW,
            )
        )
        assert result == replay
        assert result["state"] == "applied"
        assert result["snapshot"]["state"] == "ready"
        assert result["snapshot"]["stage_count"] == 5
        assert result["snapshot"]["ready_stage_count"] == 5
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count()).select_from(tables["tenant_knowledge_serving_snapshots"])
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count()).select_from(tables["tenant_knowledge_serving_stage_facts"])
                )
                == 5
            )
            stage_rows = session.execute(
                select(tables["tenant_knowledge_serving_stage_facts"]).order_by(
                    tables["tenant_knowledge_serving_stage_facts"].c.sequence
                )
            ).mappings().all()
            assert stage_rows[0]["ready_count"] == 2
            assert stage_rows[0]["warning_count"] == 0
            assert stage_rows[3]["pending_count"] == 0
            assert (
                session.scalar(
                    select(func.count()).select_from(tables["tenant_knowledge_serving_events"])
                )
                >= 1
            )
        after = body(
            operation("get_serving_summary")(engine, tenant_id="tenant-a", dataset_id="dataset-a")
        )
        assert before["tenant_id"] == after["tenant_id"] == "tenant-a"
    finally:
        engine.dispose()


def test_reads_are_strictly_tenant_scoped_and_event_chain_is_projected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = _engine(tmp_path)
    _seed_tenant_b(engine)
    _install_adapters(monkeypatch, state="lagging")
    try:
        created = _policy(engine)
        profile_id = created["profile"]["id"]
        policy_id = created["policy"]["id"]
        recorded = body(
            operation("record_knowledge_serving_snapshot")(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                dataset_id="dataset-a",
                profile_id=profile_id,
                policy_revision_id=policy_id,
                expected_profile_revision=2,
                reason="record degraded observation",
                idempotency_key="snapshot-record-lagging",
                request_id="request-stage26-record-lagging",
                now=NOW,
            )
        )
        assert recorded["snapshot"]["state"] == "degraded"
        summary = body(
            operation("get_serving_summary")(engine, tenant_id="tenant-a", dataset_id="dataset-a")
        )
        assert summary["state"] == "degraded"
        snapshots = body(
            operation("list_serving_snapshots")(
                engine, tenant_id="tenant-a", dataset_id="dataset-a"
            )
        )
        assert len(snapshots["items"]) == 1
        detail = body(
            operation("get_serving_snapshot")(
                engine,
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                snapshot_id=recorded["snapshot"]["id"],
            )
        )
        assert len(detail["stage_facts"]) == 5
        assert all(item["tenant_id"] == "tenant-a" for item in detail["stage_facts"])
        events = body(
            operation("list_serving_events")(engine, tenant_id="tenant-a", dataset_id="dataset-a")
        )
        assert events["items"]
        with pytest.raises(Exception, match="not found|tenant|scope"):
            operation("get_serving_profile")(engine, tenant_id="tenant-b", dataset_id="dataset-a")
    finally:
        engine.dispose()


def test_preview_is_zero_write_and_accepts_only_safe_stage_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, tables = _engine(tmp_path)
    _install_adapters(monkeypatch, state="blocked")
    observed_policy: dict[str, Any] = {}
    target = service()
    registry = dict(target.SERVING_ADAPTER_REGISTRY)
    source_adapter = registry["source"]

    def capture_source(**kwargs: Any) -> dict[str, Any]:
        observed_policy.update(kwargs["policy"])
        return source_adapter(**kwargs)

    registry["source"] = capture_source
    monkeypatch.setattr(target, "SERVING_ADAPTER_REGISTRY", registry)
    try:
        created = _policy(engine)
        profile_id = created["profile"]["id"]
        policy_id = created["policy"]["id"]
        activated = body(
            operation("activate_serving_policy")(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                dataset_id="dataset-a",
                policy_revision_id=policy_id,
                expected_profile_revision=2,
                expected_policy_digest=created["policy"]["policy_digest"],
                reason="activate policy before zero-write preview",
                idempotency_key="policy-activate-preview",
                request_id="request-stage26-preview-activate",
                now=NOW,
            )
        )
        assert activated["profile"]["revision"] == 3
        with Session(engine) as session:
            before = {
                name: session.scalar(select(func.count()).select_from(table))
                for name, table in tables.items()
            }
        result = body(
            operation("preview_serving_profile")(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                dataset_id="dataset-a",
                profile_id=profile_id,
                expected_profile_revision=3,
                expected_policy_digest=created["policy"]["policy_digest"],
                max_source_staleness_seconds=30,
                max_parse_lag_seconds=60,
                max_index_lag_seconds=60,
                max_failed_document_count=1,
                max_pending_index_count=1,
                require_current_release=False,
                require_passing_certification=False,
                reason="preview bounded policy impact",
                now=NOW,
            )
        )
        assert result["preview"] is True
        assert result["state"] == "blocked"
        assert len(result["stage_facts"]) == 5
        assert result["blockers"]
        assert observed_policy["max_source_staleness_seconds"] == 30
        assert observed_policy["max_parse_lag_seconds"] == 60
        assert observed_policy["max_index_lag_seconds"] == 60
        assert observed_policy["max_failed_document_count"] == 1
        assert observed_policy["max_pending_index_count"] == 1
        assert observed_policy["require_current_release"] is False
        assert observed_policy["require_passing_certification"] is False
        assert observed_policy["policy_digest"] != created["policy"]["policy_digest"]
        assert "source_payload" not in repr(result)
        with Session(engine) as session:
            after = {
                name: session.scalar(select(func.count()).select_from(table))
                for name, table in tables.items()
            }
        assert before == after
    finally:
        engine.dispose()


def test_record_operation_is_not_exposed_as_public_service_router_contract() -> None:
    from fastapi import FastAPI

    from server.enterprise_knowledge_serving_api import build_enterprise_knowledge_serving_router

    app = FastAPI()
    app.include_router(
        build_enterprise_knowledge_serving_router(
            read_engine_provider=lambda: "read",
            mutation_engine_provider=lambda: "mutation",
            service=service(),
            actor_dependency=lambda: object(),
        )
    )
    paths = set(app.openapi()["paths"])
    assert not any("record" in path or "observe" in path or "execute" in path for path in paths)
