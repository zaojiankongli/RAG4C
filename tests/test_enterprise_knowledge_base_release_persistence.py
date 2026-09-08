from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.enterprise_knowledge_base_releases import (
    ReleaseManifestConflict,
    capture_release_candidate,
    get_release,
    get_release_readiness,
    list_channel_bindings,
    list_release_channels,
    list_releases,
    promote_release,
)
from models.orm import (
    DatasetReleaseEntry,
    DatasetReleaseEvent,
    DatasetReleaseManifest,
    Document,
    TenantAuditEvent,
    TenantControlMutationRequest,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine


def capture(engine, *, key: str = "release-capture-key", reason: str = "capture candidate"):
    return capture_release_candidate(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        expected_profile_revision=4,
        expected_ownership_revision=5,
        expected_workspace_revision=2,
        expected_mutation_generation=9,
        expected_serving_generation=3,
        reason=reason,
        request_id="request-capture",
        request_ip="127.0.0.1",
        idempotency_key=key,
    )


def test_capture_persists_immutable_manifest_entries_event_audit_and_replay(tmp_path: Path) -> None:
    engine, _now = _release_engine(tmp_path)
    first = capture(engine)
    second = capture(engine)

    assert first.status == 201
    assert second.status == 201
    assert first.body == second.body
    release_id = first.body["release"]["id"]
    assert first.body["release"]["readiness_state"] == "ready"
    assert first.body["release"]["entry_count"] == 5
    assert "raw-source-secret" not in repr(first.body)
    assert "secret://parser/main" not in repr(first.body)

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetReleaseManifest)) == 1
        assert session.scalar(select(func.count()).select_from(DatasetReleaseEntry)) == 5
        assert session.scalar(select(func.count()).select_from(DatasetReleaseEvent)) == 1
        assert session.scalar(select(func.count()).select_from(TenantAuditEvent)) == 1
        assert session.scalar(select(func.count()).select_from(TenantControlMutationRequest)) == 1
        assert (
            session.scalar(
                select(DatasetReleaseManifest.manifest_digest).where(
                    DatasetReleaseManifest.id == release_id
                )
            )
            == first.body["release"]["manifest_digest"]
        )
    engine.dispose()


def test_capture_rejects_stale_revision_and_conflicting_idempotency_payload(tmp_path: Path) -> None:
    engine, _now = _release_engine(tmp_path)
    with pytest.raises(ReleaseManifestConflict, match="profile revision"):
        capture_release_candidate(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            expected_profile_revision=99,
            expected_ownership_revision=5,
            expected_workspace_revision=2,
            expected_mutation_generation=9,
            expected_serving_generation=3,
            reason="stale",
            request_id="request-stale",
            request_ip="127.0.0.1",
            idempotency_key="stale-key",
        )
    first = capture(engine, key="conflict-key", reason="first")
    assert first.status == 201
    with pytest.raises(ReleaseManifestConflict, match="idempotency"):
        capture(engine, key="conflict-key", reason="different")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetReleaseManifest)) == 1
    engine.dispose()


def test_release_reads_are_tenant_scoped_keyset_and_preserve_null_bindings(tmp_path: Path) -> None:
    engine, _now = _release_engine(tmp_path)
    captured = capture(engine)
    release_id = captured.body["release"]["id"]

    channels = list_release_channels(
        engine, tenant_id="tenant-a", actor_id="owner-a", cursor=None, limit=2
    )
    assert len(channels.body["items"]) == 2
    assert channels.body["next_cursor"]
    next_channels = list_release_channels(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        cursor=channels.body["next_cursor"],
        limit=2,
    )
    assert len(next_channels.body["items"]) == 1

    releases = list_releases(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        channel_id=None,
        cursor=None,
        limit=20,
    )
    assert [item["id"] for item in releases.body["items"]] == [release_id]
    detail = get_release(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
    )
    assert detail.body["manifest"]["id"] == release_id
    assert detail.body["entries"]["count"] == 5
    readiness = get_release_readiness(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
    )
    assert readiness.body["state"] == "ready"
    bindings = list_channel_bindings(
        engine, tenant_id="tenant-a", actor_id="owner-a", dataset_id="dataset-a"
    )
    assert bindings.body["items"] == []
    engine.dispose()


def test_capture_persists_readiness_blocker_details_for_reproducible_preflight(
    tmp_path: Path,
) -> None:
    engine, _now = _release_engine(tmp_path)
    with Session(engine) as session:
        document = session.get(Document, "document-a")
        assert document is not None
        document.indexed_revision = 0
        session.commit()
    captured = capture(engine, key="blocked-release-key", reason="capture blocked evidence")
    release_id = captured.body["release"]["id"]
    readiness = get_release_readiness(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
    ).body
    assert readiness["state"] == "blocked"
    assert readiness["blocker_count"] == 1
    assert readiness["blockers"] == [
        {
            "code": "document_index_drift",
            "resource_id": "document-a",
            "severity": "blocked",
            "facts": {"desired_index_revision": 1, "indexed_revision": 0},
        }
    ]
    engine.dispose()


def test_release_history_channel_filter_uses_append_only_channel_events(tmp_path: Path) -> None:
    engine, _now = _release_engine(tmp_path)
    captured = capture(engine, key="channel-filter-capture", reason="channel filter candidate")
    release_id = captured.body["release"]["id"]
    before = list_releases(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        channel_id="channel-development",
        cursor=None,
        limit=20,
    )
    assert before.body["items"] == []
    promote_release(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
        channel_id="channel-development",
        expected_channel_revision=1,
        expected_profile_revision=4,
        expected_ownership_revision=5,
        expected_workspace_revision=2,
        expected_serving_generation=3,
        reason="publish development",
        request_id="request-channel-filter-promote",
        request_ip="127.0.0.1",
        idempotency_key="channel-filter-promote",
    )
    after = list_releases(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        channel_id="channel-development",
        cursor=None,
        limit=20,
    )
    assert [item["id"] for item in after.body["items"]] == [release_id]
    assert after.body["items"][0]["status"] == "published"
    assert after.body["items"][0]["revision"] == 2
    assert after.body["count"] == 1
    assert after.body["summary"]["channel_id"] == "channel-development"
    assert after.body["summary"]["effective"]["release_id"] == release_id
    assert after.body["summary"]["candidate"]["release_id"] == release_id
    assert after.body["summary"]["comparison_state"] == "aligned"
    assert after.body["summary"]["configured"] == {
        "profile_revision": 4,
        "mutation_generation": 9,
        "ownership_revision": 5,
        "workspace_id": "workspace-a",
        "workspace_revision": 2,
        "policy_digest": after.body["summary"]["configured"]["policy_digest"],
    }
    unrelated = list_releases(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        channel_id="channel-testing",
        cursor=None,
        limit=20,
    )
    assert unrelated.body["items"] == []
    engine.dispose()


def test_capture_rejects_secret_like_reason_before_any_immutable_write(tmp_path: Path) -> None:
    engine, _now = _release_engine(tmp_path)
    with pytest.raises(Exception, match="credential-like"):
        capture(engine, key="secret-reason-key", reason="password=topsecret")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetReleaseManifest)) == 0
    engine.dispose()
