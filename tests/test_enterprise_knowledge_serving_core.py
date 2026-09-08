from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from core.enterprise_knowledge_serving import (
    EVIDENCE_KINDS,
    SERVING_EVENT_TYPES,
    SERVING_OVERALL_STATES,
    SERVING_STAGE_CODES,
    SERVING_STAGE_STATES,
    KnowledgeServingAuthorityInvalid,
    canonical_knowledge_serving_event,
    canonical_knowledge_serving_evidence_link,
    canonical_knowledge_serving_policy,
    canonical_knowledge_serving_snapshot,
    canonical_knowledge_serving_stage_fact,
    canonical_safe_mapping,
    derive_knowledge_serving_state,
    project_knowledge_serving_route,
)

TIME = "2026-08-30T00:00:00.000000Z"
DIGEST = "a" * 64


def policy(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "profile_id": "profile-a",
        "revision": 1,
        "max_source_staleness_seconds": 3600,
        "max_parse_lag_seconds": 1800,
        "max_index_lag_seconds": 1800,
        "max_failed_document_count": 0,
        "max_pending_index_count": 0,
        "require_current_release": True,
        "require_passing_certification": True,
    }
    value.update(overrides)
    return value


def stage(
    code: str,
    sequence: int,
    *,
    state: str = "ready",
    **overrides: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "profile_id": "profile-a",
        "snapshot_id": "snapshot-a",
        "stage_code": code,
        "sequence": sequence,
        "state": state,
        "item_count": 10,
        "ready_count": 10 if code == "source" else 0,
        "warning_count": 0,
        "pending_count": 0,
        "error_count": 0,
        "lag_seconds": 0,
        "expected_revision": 2,
        "observed_revision": 2,
        "expected_digest": DIGEST,
        "observed_digest": DIGEST,
        "safe_error_code": None,
        "safe_error": None,
        "observed_at": TIME,
    }
    value.update(overrides)
    return value


def snapshot(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "snapshot-a",
        "tenant_id": "tenant-a",
        "profile_id": "profile-a",
        "policy_revision_id": "policy-revision-a",
        "observation_key": "observation-a",
        "source_count": 10,
        "ready_source_count": 10,
        "stale_source_count": 0,
        "active_document_count": 10,
        "failed_document_count": 0,
        "pending_index_count": 0,
        "expected_serving_generation": 2,
        "observed_serving_generation": 2,
        "current_release_id": "release-a",
        "current_certification_id": "cert-a",
        "as_of": TIME,
        "created_at": TIME,
        "created_by": "owner-a",
    }
    value.update(overrides)
    return value


def test_stage26_has_exact_allowlists_and_no_generic_execution_codes() -> None:
    assert SERVING_STAGE_CODES == ("source", "parse", "chunk", "index", "serve")
    assert SERVING_STAGE_STATES == {"ready", "lagging", "blocked", "missing", "unavailable"}
    assert SERVING_OVERALL_STATES == {"ready", "degraded", "blocked", "unavailable"}
    assert EVIDENCE_KINDS == {
        "source",
        "source_sync_run",
        "document",
        "ingest_attempt",
        "chunk_head",
        "index_operation",
        "release",
        "certification",
        "task",
    }
    assert SERVING_EVENT_TYPES == {
        "profile_created",
        "policy_revision_created",
        "policy_activated",
        "snapshot_recorded",
        "stage_degraded",
        "stage_blocked",
        "service_recovered",
    }
    assert not any("execute" in value or "webhook" in value for value in SERVING_EVENT_TYPES)


def test_policy_is_canonical_and_digest_fenced() -> None:
    first = canonical_knowledge_serving_policy(policy())
    assert first["policy_digest"] == first["policy_digest"].lower()
    assert len(first["policy_digest"]) == 64
    assert (
        canonical_knowledge_serving_policy({**policy(), "policy_digest": first["policy_digest"]})
        == first
    )
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="policy_digest"):
        canonical_knowledge_serving_policy({**policy(), "policy_digest": "f" * 64})
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="threshold|nonnegative"):
        canonical_knowledge_serving_policy(policy(max_index_lag_seconds=-1))
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="boolean"):
        canonical_knowledge_serving_policy(policy(require_current_release=1))


def test_exact_five_stage_facts_derive_overall_state_fail_closed() -> None:
    ready = [
        canonical_knowledge_serving_stage_fact(stage(code, index))
        for index, code in enumerate(SERVING_STAGE_CODES, start=1)
    ]
    assert derive_knowledge_serving_state(ready) == "ready"
    assert (
        derive_knowledge_serving_state(
            [
                canonical_knowledge_serving_stage_fact(
                    stage(code, index, state="lagging" if code == "parse" else "ready")
                )
                for index, code in enumerate(SERVING_STAGE_CODES, start=1)
            ]
        )
        == "degraded"
    )
    assert (
        derive_knowledge_serving_state(
            [
                canonical_knowledge_serving_stage_fact(
                    stage(code, index, state="blocked" if code == "index" else "ready")
                )
                for index, code in enumerate(SERVING_STAGE_CODES, start=1)
            ]
        )
        == "blocked"
    )
    assert (
        derive_knowledge_serving_state(
            [
                canonical_knowledge_serving_stage_fact(
                    stage(code, index, state="missing" if code == "source" else "ready")
                )
                for index, code in enumerate(SERVING_STAGE_CODES, start=1)
            ]
        )
        == "unavailable"
    )
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="exactly five|sequence"):
        derive_knowledge_serving_state(ready[:-1])
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="stage|sequence"):
        canonical_knowledge_serving_stage_fact(stage("parse", 1))


def test_snapshot_state_and_counts_are_derived_from_stage_facts() -> None:
    facts = [
        canonical_knowledge_serving_stage_fact(stage(code, index))
        for index, code in enumerate(SERVING_STAGE_CODES, start=1)
    ]
    result = canonical_knowledge_serving_snapshot(
        snapshot(), stage_facts=facts, evidence_links=[]
    )
    assert result["state"] == "ready"
    assert result["stage_count"] == 5
    assert result["ready_stage_count"] == 5
    assert result["blocked_stage_count"] == 0
    assert len(result["snapshot_digest"]) == 64
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="state|stage_count"):
        canonical_knowledge_serving_snapshot(
            snapshot(state="blocked", stage_count=1), stage_facts=facts, evidence_links=[]
        )


def test_snapshot_requires_owned_stage_facts_and_commits_their_digests() -> None:
    facts = [
        canonical_knowledge_serving_stage_fact(stage(code, index))
        for index, code in enumerate(SERVING_STAGE_CODES, start=1)
    ]
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="stage facts|required"):
        canonical_knowledge_serving_snapshot(snapshot(), evidence_links=[])
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="identity|snapshot"):
        canonical_knowledge_serving_snapshot(
            snapshot(),
            stage_facts=[
                {**fact, "snapshot_id": "snapshot-other", "stage_digest": None}
                if fact["stage_code"] == "source"
                else fact
                for fact in facts
            ],
            evidence_links=[],
        )

    first = canonical_knowledge_serving_snapshot(
        snapshot(), stage_facts=facts, evidence_links=[]
    )
    changed_facts = [
        canonical_knowledge_serving_stage_fact(
            {
                **fact,
                "observed_digest": "b" * 64,
                "stage_digest": None,
            }
        )
        if fact["stage_code"] == "chunk"
        else fact
        for fact in facts
    ]
    second = canonical_knowledge_serving_snapshot(
        snapshot(), stage_facts=changed_facts, evidence_links=[]
    )
    assert first["snapshot_digest"] != second["snapshot_digest"]

    owned_facts = [
        canonical_knowledge_serving_stage_fact({**stage(code, index), "id": f"stage-{code}"})
        for index, code in enumerate(SERVING_STAGE_CODES, start=1)
    ]
    evidence = canonical_knowledge_serving_evidence_link(
        {
            "tenant_id": "tenant-a",
            "profile_id": "profile-a",
            "snapshot_id": "snapshot-a",
            "stage_fact_id": "stage-source",
            "evidence_kind": "source",
            "resource_id": "source-a",
            "resource_revision": 1,
            "resource_digest": DIGEST,
            "route_code": "knowledge_sources",
            "safe_label": "Source evidence",
        }
    )
    with_evidence = canonical_knowledge_serving_snapshot(
        snapshot(), stage_facts=owned_facts, evidence_links=[evidence]
    )
    changed_evidence = canonical_knowledge_serving_evidence_link(
        {**evidence, "safe_label": "Updated source evidence", "evidence_digest": None}
    )
    with_changed_evidence = canonical_knowledge_serving_snapshot(
        snapshot(), stage_facts=owned_facts, evidence_links=[changed_evidence]
    )
    assert with_evidence["snapshot_digest"] != with_changed_evidence["snapshot_digest"]
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="evidence|ownership|stage fact"):
        canonical_knowledge_serving_snapshot(
            snapshot(),
            stage_facts=owned_facts,
            evidence_links=[
                canonical_knowledge_serving_evidence_link(
                    {**evidence, "stage_fact_id": "stage-other", "evidence_digest": None}
                )
            ],
        )


def test_evidence_links_and_routes_are_allowlisted_and_encoded() -> None:
    link = canonical_knowledge_serving_evidence_link(
        {
            "tenant_id": "tenant-a",
            "profile_id": "profile-a",
            "snapshot_id": "snapshot-a",
            "stage_fact_id": "stage-a",
            "evidence_kind": "task",
            "resource_id": "task-a&next=other",
            "resource_revision": 3,
            "resource_digest": DIGEST,
            "route_code": "enterprise_tasks",
            "safe_label": "Task failed",
        }
    )
    assert len(link["evidence_digest"]) == 64
    route = project_knowledge_serving_route(link)
    assert route["code"] == "enterprise_tasks"
    assert "task-a%26next%3Dother" in route["href"]
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="route|allow"):
        canonical_knowledge_serving_evidence_link(
            {
                **link,
                "evidence_digest": None,
                "route_code": "https://evil.test/execute",
            }
        )
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="resource|safe"):
        canonical_knowledge_serving_evidence_link(
            {**link, "evidence_digest": None, "resource_id": "https://evil.test/resource"}
        )
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="resource|safe"):
        canonical_knowledge_serving_evidence_link(
            {**link, "evidence_digest": None, "resource_id": "r" * 129}
        )


@pytest.mark.parametrize(
    ("kind", "route_code", "query_parameter"),
    (
        ("source", "knowledge_sources", "source"),
        ("source_sync_run", "knowledge_sources", "sync"),
        ("document", "knowledge_documents", "document"),
        ("ingest_attempt", "knowledge_documents", "document"),
        ("chunk_head", "knowledge_documents", "document"),
        ("index_operation", "knowledge_indexing", "operation"),
        ("release", "knowledge_base_releases", "release"),
        ("certification", "release_quality", "certification"),
        ("task", "enterprise_tasks", "task"),
    ),
)
def test_every_evidence_kind_preserves_encoded_resource_identity(
    kind: str, route_code: str, query_parameter: str
) -> None:
    resource_id = f"{kind}-resource&next=other"
    link = canonical_knowledge_serving_evidence_link(
        {
            "tenant_id": "tenant-a",
            "profile_id": "profile-a",
            "snapshot_id": "snapshot-a",
            "stage_fact_id": "stage-a",
            "evidence_kind": kind,
            "resource_id": resource_id,
            "resource_revision": 1,
            "resource_digest": DIGEST,
            "route_code": route_code,
            "safe_label": f"{kind} evidence",
        }
    )
    route = project_knowledge_serving_route(link)
    assert route["code"] == route_code
    assert f"{query_parameter}={kind}-resource%26next%3Dother" in route["href"]


def test_event_chain_and_safe_mapping_reject_durable_leaks() -> None:
    first = canonical_knowledge_serving_event(
        {
            "id": "event-a",
            "tenant_id": "tenant-a",
            "profile_id": "profile-a",
            "snapshot_id": "snapshot-a",
            "stream_key": "profile:profile-a",
            "sequence": 1,
            "event_type": "profile_created",
            "previous_event_digest": None,
            "actor_id": "owner-a",
            "request_id": "request-a",
            "safe_snapshot_json": {"status": "draft", "revision": 1},
            "occurred_at": TIME,
        }
    )
    second = canonical_knowledge_serving_event(
        {
            "id": "event-b",
            "tenant_id": "tenant-a",
            "profile_id": "profile-a",
            "snapshot_id": "snapshot-a",
            "stream_key": "profile:profile-a",
            "sequence": 2,
            "event_type": "policy_revision_created",
            "previous_event_digest": first["event_digest"],
            "actor_id": "owner-a",
            "request_id": "request-b",
            "safe_snapshot_json": {"policy_revision": 2},
            "occurred_at": TIME,
        }
    )
    assert second["previous_event_digest"] == first["event_digest"]
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="previous|sequence"):
        canonical_knowledge_serving_event(
            {**second, "previous_event_digest": None, "event_digest": None}
        )
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="forbidden|unsafe|SQL"):
        canonical_safe_mapping({"apiKey": "redacted"})
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="forbidden|unsafe|SQL"):
        canonical_safe_mapping({"reason_code": "SELECT * FROM users"})
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="forbidden|unsafe"):
        canonical_safe_mapping({"source_url": "https://example.test"})
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="safe integer|precision|integer"):
        canonical_safe_mapping({"sequence": 2**53})
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="empty|unsafe"):
        canonical_safe_mapping({"note": "   "})
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="safe integer|precision|integer"):
        canonical_knowledge_serving_stage_fact(stage("source", 1, item_count=2**53))
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="first event|profile_created"):
        canonical_knowledge_serving_event(
            {
                **first,
                "event_type": "policy_revision_created",
                "event_digest": None,
            }
        )


def test_safe_mapping_cross_language_bounds_are_exact() -> None:
    nested: dict[str, object] = {}
    for _ in range(12):
        nested = {"level": nested}
    assert canonical_safe_mapping(nested) == nested

    too_deep: dict[str, object] = {}
    for _ in range(13):
        too_deep = {"level": too_deep}
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="depth"):
        canonical_safe_mapping(too_deep)

    exactly_64_items = {f"item_{index}": index for index in range(63)}
    assert canonical_safe_mapping(exactly_64_items) == exactly_64_items
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="item count"):
        canonical_safe_mapping({f"item_{index}": index for index in range(64)})

    json_overhead = len(json.dumps({"note": ""}, separators=(",", ":")).encode("utf-8"))
    exact_utf8_limit = {"note": "a" * (16_384 - json_overhead)}
    assert canonical_safe_mapping(exact_utf8_limit) == exact_utf8_limit
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="UTF-8 byte limit"):
        canonical_safe_mapping({"note": "a" * (16_385 - json_overhead)})


def test_pure_module_has_no_orm_server_or_dynamic_execution_imports() -> None:
    tree = ast.parse(Path("core/enterprise_knowledge_serving.py").read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(name.startswith(("sqlalchemy", "models", "server")) for name in imports)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "eval" not in calls
    assert "exec" not in calls
    assert "__import__" not in calls



def test_snapshot_counters_are_derived_from_exact_stage_fact_metrics() -> None:
    facts = [
        canonical_knowledge_serving_stage_fact(
            stage(
                "source",
                1,
                state="lagging",
                item_count=7,
                ready_count=5,
                warning_count=2,
                error_count=2,
            )
        ),
        canonical_knowledge_serving_stage_fact(
            stage(
                "parse",
                2,
                state="lagging",
                item_count=11,
                warning_count=3,
                error_count=3,
            )
        ),
        canonical_knowledge_serving_stage_fact(
            stage("chunk", 3, item_count=11),
        ),
        canonical_knowledge_serving_stage_fact(
            stage("index", 4, state="lagging", item_count=11, pending_count=4, error_count=0),
        ),
        canonical_knowledge_serving_stage_fact(
            stage("serve", 5, state="lagging", expected_revision=9, observed_revision=8),
        ),
    ]
    result = canonical_knowledge_serving_snapshot(
        snapshot(
            source_count=7,
            ready_source_count=5,
            stale_source_count=2,
            active_document_count=11,
            failed_document_count=3,
            pending_index_count=4,
            expected_serving_generation=9,
            observed_serving_generation=8,
        ),
        stage_facts=facts,
        evidence_links=[],
    )
    assert result["source_count"] == 7
    assert result["ready_source_count"] == 5
    assert result["stale_source_count"] == 2
    assert result["active_document_count"] == 11
    assert result["failed_document_count"] == 3
    assert result["pending_index_count"] == 4
    assert result["expected_serving_generation"] == 9
    assert result["observed_serving_generation"] == 8
    assert "ready_count" in facts[0]
    with pytest.raises(KnowledgeServingAuthorityInvalid, match="derived|counter"):
        canonical_knowledge_serving_snapshot(
            snapshot(source_count=8, ready_source_count=5, stale_source_count=2),
            stage_facts=facts,
            evidence_links=[],
        )
