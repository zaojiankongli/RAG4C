from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.enterprise_content_recovery import (
    ContentRecoveryAuthorityInvalid,
    canonical_purge_request_digest,
    canonical_recycle_key,
    canonical_recycle_snapshot,
    canonical_recovery_event,
    project_recovery_route,
)

UTC = timezone.utc


def recycle_snapshot(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "recycle_entry_id": "entry-a",
        "dataset_id": "dataset-a",
        "document_id": "document-a",
        "recycle_generation": 1,
        "status": "recycled",
        "revision": 3,
        "document_mutation_generation": 7,
        "original_lifecycle_state": "active",
        "original_retrieval_enabled": True,
        "retention_days_snapshot": 30,
        "recycled_at": "2026-08-29T20:00:00.123456+08:00",
        "purge_eligible_at": "2026-09-28T20:00:00.123456+08:00",
    }
    value.update(overrides)
    return value


def purge_request(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "recycle_entry_id": "entry-a",
        "dataset_id": "dataset-a",
        "document_id": "document-a",
        "entry_revision": 3,
        "purge_eligible_at": "2026-08-29T20:00:00.123456+08:00",
        "retention_days_snapshot": 30,
        "legal_hold_count": 0,
    }
    value.update(overrides)
    return value


def test_recycle_key_is_tenant_safe_and_canonical() -> None:
    assert canonical_recycle_key("dataset-a", "document-a") == "dataset-a:document-a"
    assert (
        canonical_recycle_key(
            {"tenant_id": "tenant-a", "dataset_id": "dataset-a", "document_id": "document-a"}
        )
        == "dataset-a:document-a"
    )
    assert (
        canonical_recycle_key(
            dataset_id="dataset-a",
            document_id="document-a",
            active_recycle_key="dataset-a:document-a",
        )
        == "dataset-a:document-a"
    )

    with pytest.raises(ContentRecoveryAuthorityInvalid, match="identity"):
        canonical_recycle_key("dataset-a", "document-a:other")


def test_recycle_snapshot_is_deterministic_and_normalizes_utc_microseconds() -> None:
    first = canonical_recycle_snapshot(recycle_snapshot())
    second = canonical_recycle_snapshot(
        dict(
            reversed(
                list(
                    recycle_snapshot(
                        recycled_at=datetime(2026, 8, 29, 12, 0, 0, 123456, tzinfo=UTC),
                        purge_eligible_at=datetime(2026, 9, 28, 12, 0, 0, 123456, tzinfo=UTC),
                    ).items()
                )
            )
        )
    )
    assert first == second
    assert first["recycled_at"] == "2026-08-29T12:00:00.123456Z"
    assert first["purge_eligible_at"] == "2026-09-28T12:00:00.123456Z"
    assert first["active_recycle_key"] == "dataset-a:document-a"
    assert len(first["snapshot_digest"]) == 64
    assert (
        first["snapshot_digest"]
        == canonical_recycle_snapshot(recycle_snapshot())["snapshot_digest"]
    )


def test_recycle_snapshot_enforces_lifecycle_and_exact_types() -> None:
    with pytest.raises(ContentRecoveryAuthorityInvalid, match="exact integer"):
        canonical_recycle_snapshot(recycle_snapshot(recycle_generation=True))
    with pytest.raises(ContentRecoveryAuthorityInvalid, match="exact boolean"):
        canonical_recycle_snapshot(recycle_snapshot(original_retrieval_enabled=1))
    with pytest.raises(ContentRecoveryAuthorityInvalid, match="lifecycle"):
        canonical_recycle_snapshot(recycle_snapshot(original_lifecycle_state="deleted"))
    with pytest.raises(ContentRecoveryAuthorityInvalid, match="status"):
        canonical_recycle_snapshot(recycle_snapshot(status="active"))
    with pytest.raises(ContentRecoveryAuthorityInvalid, match="retention"):
        canonical_recycle_snapshot(recycle_snapshot(retention_days_snapshot=0))


def test_purge_request_digest_is_domain_separated_order_independent_and_fenced() -> None:
    first = canonical_purge_request_digest(purge_request())
    second = canonical_purge_request_digest(dict(reversed(list(purge_request().items()))))
    assert first == second
    assert len(first) == 64
    assert first != canonical_purge_request_digest(purge_request(entry_revision=4))

    with pytest.raises(ContentRecoveryAuthorityInvalid, match="legal hold"):
        canonical_purge_request_digest(purge_request(legal_hold_count=1))
    with pytest.raises(ContentRecoveryAuthorityInvalid, match="exact integer"):
        canonical_purge_request_digest(purge_request(entry_revision=True))


def test_recovery_event_builds_contiguous_hash_chain_and_rejects_forgery() -> None:
    first = canonical_recovery_event(
        tenant_id="tenant-a",
        recycle_entry_id="entry-a",
        dataset_id="dataset-a",
        document_id="document-a",
        sequence=1,
        event_type="recycled",
        previous_event_digest=None,
        actor_id="account-a",
        request_id="request-a",
        safe_snapshot={"status": "recycled", "revision": 1},
        occurred_at="2026-08-29T20:00:00.123456+08:00",
    )
    assert first["previous_event_digest"] is None
    assert first["occurred_at"] == "2026-08-29T12:00:00.123456Z"
    assert len(first["event_digest"]) == 64

    second = canonical_recovery_event(
        tenant_id="tenant-a",
        recycle_entry_id="entry-a",
        dataset_id="dataset-a",
        document_id="document-a",
        sequence=2,
        event_type="restored",
        previous_event_digest=first["event_digest"],
        actor_id="account-a",
        request_id="request-b",
        safe_snapshot={"status": "restored", "revision": 2},
        occurred_at="2026-08-29T12:01:00+00:00",
    )
    assert second["previous_event_digest"] == first["event_digest"]
    assert second["event_digest"] != first["event_digest"]

    with pytest.raises(ContentRecoveryAuthorityInvalid, match="first event"):
        canonical_recovery_event(
            tenant_id="tenant-a",
            recycle_entry_id="entry-a",
            dataset_id="dataset-a",
            document_id="document-a",
            sequence=1,
            event_type="restored",
            previous_event_digest=None,
            actor_id="account-a",
            request_id="request-a",
            safe_snapshot={},
            occurred_at="2026-08-29T12:00:00Z",
        )
    with pytest.raises(ContentRecoveryAuthorityInvalid, match="event_digest"):
        canonical_recovery_event(
            tenant_id="tenant-a",
            recycle_entry_id="entry-a",
            dataset_id="dataset-a",
            document_id="document-a",
            sequence=1,
            event_type="recycled",
            previous_event_digest=None,
            event_digest="f" * 64,
            actor_id="account-a",
            request_id="request-a",
            safe_snapshot={},
            occurred_at="2026-08-29T12:00:00Z",
        )


def test_recovery_event_accepts_event_sequence_alias_and_requires_valid_previous_digest() -> None:
    event = canonical_recovery_event(
        {
            "tenant_id": "tenant-a",
            "recycle_entry_id": "entry-a",
            "dataset_id": "dataset-a",
            "document_id": "document-a",
            "event_sequence": 1,
            "event_type": "recycled",
            "previous_digest": None,
            "actor_id": "system:recovery",
            "request_id": "request-a",
            "safe_snapshot_json": {"status": "recycled"},
            "occurred_at": "2026-08-29T12:00:00Z",
        }
    )
    assert event["sequence"] == 1
    assert event["event_digest"]

    with pytest.raises(ContentRecoveryAuthorityInvalid, match="SHA-256"):
        canonical_recovery_event(
            tenant_id="tenant-a",
            recycle_entry_id="entry-a",
            dataset_id="dataset-a",
            document_id="document-a",
            sequence=2,
            event_type="hold_applied",
            previous_event_digest="not-a-digest",
            actor_id="account-a",
            request_id="request-a",
            safe_snapshot={},
            occurred_at="2026-08-29T12:00:00Z",
        )


@pytest.mark.parametrize(
    "unsafe",
    [
        {"raw_content": "secret customer document body"},
        {"metadata": {"classification": "private"}},
        {"source_url": "https://example.test/private"},
        {"query": "customer password=secret"},
        {"reviewer_note": "keep this note"},
        {"approval_ticket": "opaque-ticket-123"},
        {"access_token": "bearer topsecret"},
        {"credential": "secret://vault/recovery"},
    ],
)
def test_all_recovery_authority_boundaries_reject_unsafe_fields(
    unsafe: dict[str, object],
) -> None:
    with pytest.raises(ContentRecoveryAuthorityInvalid):
        canonical_recycle_snapshot(recycle_snapshot(**unsafe))
    with pytest.raises(ContentRecoveryAuthorityInvalid):
        canonical_purge_request_digest(purge_request(**unsafe))
    with pytest.raises(ContentRecoveryAuthorityInvalid):
        canonical_recovery_event(
            tenant_id="tenant-a",
            recycle_entry_id="entry-a",
            dataset_id="dataset-a",
            document_id="document-a",
            sequence=1,
            event_type="recycled",
            previous_event_digest=None,
            actor_id="account-a",
            request_id="request-a",
            safe_snapshot=unsafe,
            occurred_at="2026-08-29T12:00:00Z",
        )


def test_recovery_routes_are_allow_listed_and_exact() -> None:
    recycle = project_recovery_route(
        "enterprise_recycle_bin",
        {"tenant_id": "tenant-a", "entry_id": "entry-a"},
    )
    assert recycle == {
        "target_route_code": "enterprise_recycle_bin",
        "target_route_params_json": {"tenant_id": "tenant-a", "entry_id": "entry-a"},
    }
    approval = project_recovery_route(
        route_code="enterprise_approval",
        params={"tenant_id": "tenant-a", "approval_request_id": "approval-a"},
    )
    assert approval["target_route_code"] == "enterprise_approval"

    with pytest.raises(ContentRecoveryAuthorityInvalid, match="route"):
        project_recovery_route("/enterprise/recycle-bin", {})
    with pytest.raises(ContentRecoveryAuthorityInvalid, match="forbidden"):
        project_recovery_route(
            "enterprise_approval",
            {"tenant_id": "tenant-a", "approval_request_id": "approval-a", "token": "x"},
        )
    with pytest.raises(ContentRecoveryAuthorityInvalid, match="parameter"):
        project_recovery_route("enterprise_recycle_bin", {"tenant_id": "tenant-a"})


def test_recovery_digest_domain_separation_is_not_cross_domain() -> None:
    from core.enterprise_content_recovery import canonical_recovery_digest

    value = {"tenant_id": "tenant-a", "document_id": "document-a"}
    assert canonical_recovery_digest("recycle", value) != canonical_recovery_digest(
        "purge-request", value
    )


def test_recovery_authority_module_has_no_orm_imports() -> None:
    tree = ast.parse(Path("core/enterprise_content_recovery.py").read_text(encoding="utf-8"))
    imported_modules = {
        node.module.casefold()
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imported_names = {
        alias.name.casefold()
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(
        module == "sqlalchemy" or module.startswith("sqlalchemy.") for module in imported_modules
    )
    assert "orm" not in imported_names
