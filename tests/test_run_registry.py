from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from datetime import datetime, timedelta, timezone
from threading import Thread
from time import perf_counter
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from config.settings import RunHistorySettings
from core.run_events import EventError, RunEvent, RunEventSequencer
from core.run_history_store import RunHistoryStore
from core.run_registry import (
    BoundRunContext,
    RegistryIdentity,
    RunListQuery,
    RunRegistry,
    derive_tenant_scope,
    fingerprint_query,
    sanitize_run_event,
)
from rag_topology import build_cache_replay_topology, topology_dict


def _topology(*, revision: str = "rev-test") -> dict[str, Any]:
    return {
        "revision": revision,
        "id": "rag.query",
        "executor": "sequential_stream",
        "nodes": [
            {
                "id": "search",
                "label": "Search",
                "group": "retrieve",
                "description": "Retrieve candidate chunks.",
                "optional": False,
                "repeatable": False,
                "available": True,
                "attributes": {},
            }
        ],
        "edges": [],
    }


def _event(
    *,
    event_type: str = "run.started",
    attributes: dict[str, Any] | None = None,
    error: EventError | None = None,
) -> RunEvent:
    node_event = event_type.startswith("node.") or event_type.startswith("retry.")
    return RunEvent(
        run_id="run-privacy-boundary",
        seq=1,
        occurred_at=datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
        elapsed_ms=12.5,
        topology_id="rag.query",
        topology_revision="rev-test",
        type=event_type,
        node_id="search" if node_event else None,
        attempt=1 if node_event else None,
        duration_ms=4.0 if node_event else None,
        attributes=attributes or {},
        error=error,
    )


def test_approved_defaults() -> None:
    value = RunHistorySettings()

    assert (value.enabled, value.persistence_enabled) == (True, True)
    assert (value.sqlite_path, value.memory_max_active_runs) == (
        "data/run-history.sqlite3",
        128,
    )
    assert (value.memory_max_recent_runs, value.memory_max_events_per_run) == (512, 1024)
    assert (value.memory_max_events_total, value.memory_terminal_ttl_s) == (32768, 21600)
    assert (value.max_event_json_bytes, value.max_topology_json_bytes) == (16384, 131072)
    assert (value.writer_queue_capacity, value.writer_batch_size) == (8192, 64)
    assert (value.writer_flush_ms, value.writer_shutdown_grace_ms) == (100, 2000)
    assert (value.retention_days, value.max_persisted_runs) == (30, 100000)
    assert (value.cleanup_interval_s, value.cleanup_batch_size) == (600, 1000)
    assert (value.heartbeat_interval_s, value.worker_stale_after_s) == (5, 30)
    assert (value.stuck_after_s, value.slow_threshold_ms) == (300, 30000)
    assert (value.api_default_page_size, value.api_max_page_size) == (50, 100)
    assert (value.events_max_page_size, value.cursor_ttl_s) == (500, 3600)
    assert (value.long_poll_max_ms, value.long_poll_max_clients) == (25000, 64)
    assert value.ops_bearer_token is None
    assert value.fingerprint_secret is None


@pytest.mark.parametrize(
    "updates",
    [
        {"memory_max_active_runs": 0},
        {"memory_max_events_per_run": 1},
        {"max_event_json_bytes": 1023},
        {"max_topology_json_bytes": 4095},
        {"api_max_page_size": 101},
        {"long_poll_max_ms": 25001},
        {"api_default_page_size": 51, "api_max_page_size": 50},
        {"writer_batch_size": 65, "writer_queue_capacity": 64},
        {"heartbeat_interval_s": 5, "worker_stale_after_s": 5},
    ],
)
def test_invalid_bounds_fail_configuration_loading(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RunHistorySettings(**updates)


def test_registry_secrets_are_secret_str_values() -> None:
    value = RunHistorySettings(
        ops_bearer_token="operator-secret",
        fingerprint_secret="fingerprint-secret",
    )

    assert isinstance(value.ops_bearer_token, SecretStr)
    assert isinstance(value.fingerprint_secret, SecretStr)
    assert "operator-secret" not in repr(value)
    assert "fingerprint-secret" not in repr(value)


def test_identity_and_bound_context_are_immutable() -> None:
    identity = RegistryIdentity(
        boot_id="boot",
        worker_id="worker",
        scope_key=b"s" * 32,
        cursor_key=b"c" * 32,
        tenant_scope_stability="installation",
    )
    context = BoundRunContext(tenant_scope="opaque", query_fingerprint=None)

    with pytest.raises(FrozenInstanceError):
        identity.boot_id = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        context.tenant_scope = "changed"  # type: ignore[misc]


def test_scope_and_fingerprint_are_domain_separated_hmacs() -> None:
    scope = derive_tenant_scope("Acme-研发", b"s" * 32)

    assert scope == "-WleFqufVGjeE6v8ccF1hkM6"
    assert scope == derive_tenant_scope("Acme-研发", b"s" * 32)
    assert scope != derive_tenant_scope("Acme-研发", b"t" * 32)
    assert "Acme" not in scope
    assert fingerprint_query("  Ａ\u3000B\nC  ", b"f" * 32) == "_Ouj0MTVK7Cgn4lcA0w0oA"
    assert fingerprint_query("  Ａ\u3000B\nC  ", b"f" * 32) == fingerprint_query("A B C", b"f" * 32)
    assert fingerprint_query("A B C", b"f" * 32) != derive_tenant_scope("A B C", b"f" * 32)


@pytest.mark.parametrize(
    ("attribute", "sentinel"),
    [
        ("query", "SENTINEL_QUERY"),
        ("answer", "SENTINEL_ANSWER"),
        ("prompt", "SENTINEL_PROMPT"),
        ("acl", "SENTINEL_ACL"),
        ("chunk_id", "SENTINEL_CHUNK"),
        ("document_path", "SENTINEL_PATH"),
        ("api_key", "SENTINEL_KEY"),
        ("token", "SENTINEL_TOKEN"),
        ("exception_message", "SENTINEL_EXCEPTION"),
    ],
)
def test_unknown_sensitive_attribute_rejects_registry_copy(attribute: str, sentinel: str) -> None:
    hostile = _event(attributes={attribute: sentinel})

    result = sanitize_run_event(hostile, RunHistorySettings())

    assert result.event is None
    assert result.reason == "attributes_unknown_key"
    assert sentinel not in repr(result)


def test_unknown_envelope_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    event = _event(attributes={"outcome": "unknown"})

    def hostile_copy(_event: RunEvent) -> dict[str, Any]:
        return {**event.model_dump(mode="json", exclude_none=True), "query": "ENVELOPE_SECRET"}

    monkeypatch.setattr("core.run_registry.run_event_dict", hostile_copy)
    result = sanitize_run_event(event, RunHistorySettings())

    assert result.event is None
    assert result.reason == "envelope_unknown_key"
    assert "ENVELOPE_SECRET" not in repr(result)


def test_allowed_event_is_deeply_detached_from_upstream_event() -> None:
    event = _event(
        attributes={
            "outcome": "answered",
            "reason": {"safe": ["one", {"nested": True}]},
            "topology": _topology(),
        }
    )

    result = sanitize_run_event(event, RunHistorySettings())

    assert result.reason is None
    assert result.event is not None
    result.event["attributes"]["reason"]["safe"][1]["nested"] = False
    result.event["attributes"]["topology"]["nodes"][0]["label"] = "Changed"
    assert event.attributes["reason"]["safe"][1]["nested"] is True
    assert event.attributes["topology"]["nodes"][0]["label"] == "Search"


def test_allowed_attributes_and_error_are_preserved_without_message() -> None:
    event = _event(
        event_type="node.failed",
        attributes={
            "route": "hybrid",
            "chunk_count": 4,
            "recoverable": True,
            "reason": "dependency_unavailable",
        },
        error=EventError(type="TimeoutError", code="dependency_timeout", recoverable=True),
    )

    result = sanitize_run_event(event, RunHistorySettings())

    assert result.reason is None
    assert result.event is not None
    assert result.event["attributes"] == {
        "route": "hybrid",
        "chunk_count": 4,
        "recoverable": True,
        "reason": "dependency_unavailable",
    }
    assert result.event["error"] == {
        "type": "TimeoutError",
        "code": "dependency_timeout",
        "recoverable": True,
    }


def test_unknown_error_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    event = _event(
        event_type="node.failed",
        error=EventError(type="RuntimeError", code="failed", recoverable=False),
    )
    payload = event.model_dump(mode="json", exclude_none=True)
    payload["error"]["message"] = "SENTINEL_EXCEPTION_MESSAGE"
    monkeypatch.setattr("core.run_registry.run_event_dict", lambda _event: payload)

    result = sanitize_run_event(event, RunHistorySettings())

    assert result.event is None
    assert result.reason == "error_unknown_key"
    assert "SENTINEL_EXCEPTION_MESSAGE" not in repr(result)


def test_overlong_regular_string_is_replaced_without_prefix_leak() -> None:
    secret_prefix = "SENTINEL_PREFIX"
    event = _event(attributes={"reason": secret_prefix + "x" * 96})

    result = sanitize_run_event(event, RunHistorySettings())

    assert result.reason is None
    assert result.event is not None
    assert result.event["attributes"]["reason"] == "[redacted:length]"
    assert secret_prefix not in repr(result)


@pytest.mark.parametrize(
    ("attributes", "reason"),
    [
        ({"chunk_count": -1}, "attributes_negative_number"),
        ({"reason": [*range(33)]}, "attributes_array_too_large"),
        ({"reason": {str(index): index for index in range(65)}}, "attributes_object_too_large"),
        ({"reason": {"a": {"b": {"c": {"d": {"e": True}}}}}}, "attributes_too_deep"),
    ],
)
def test_attribute_scalar_and_collection_limits_fail_closed(
    attributes: dict[str, Any], reason: str
) -> None:
    result = sanitize_run_event(_event(attributes=attributes), RunHistorySettings())

    assert result.event is None
    assert result.reason == reason


def test_non_finite_number_from_defensive_copy_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _event(attributes={"confidence": 0.5})
    payload = event.model_dump(mode="json", exclude_none=True)
    payload["attributes"]["confidence"] = float("inf")
    monkeypatch.setattr("core.run_registry.run_event_dict", lambda _event: payload)

    result = sanitize_run_event(event, RunHistorySettings())

    assert result.event is None
    assert result.reason == "attributes_non_finite_number"


def test_event_json_byte_cap_rejects_registry_copy() -> None:
    attributes = {
        key: "x" * 96
        for key in (
            "outcome",
            "reason",
            "request_path",
            "cache_level",
            "mode",
            "executor",
            "executor_requested",
            "executor_used",
            "delivery",
            "singleflight",
            "route",
            "effective",
            "effective_route",
            "fallback_route",
            "configured_route",
        )
    }

    result = sanitize_run_event(
        _event(attributes=attributes),
        RunHistorySettings(max_event_json_bytes=1024),
    )

    assert result.event is None
    assert result.reason == "event_json_too_large"


def test_valid_topology_is_accepted_as_one_revision_consistent_unit() -> None:
    result = sanitize_run_event(
        _event(attributes={"executor": "sequential_stream", "topology": _topology()}),
        RunHistorySettings(),
    )

    assert result.reason is None
    assert result.event is not None
    assert result.event["attributes"]["topology"] == _topology()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda topology: topology.update({"query": "SENTINEL_TOPOLOGY"}), "topology_unknown_key"),
        (lambda topology: topology.update({"revision": "other"}), "topology_revision_mismatch"),
        (
            lambda topology: topology["nodes"][0].update({"label": "x" * 129}),
            "topology_string_too_long",
        ),
        (
            lambda topology: topology["nodes"][0].update({"query": "SENTINEL_NODE"}),
            "topology_node_unknown_key",
        ),
    ],
)
def test_invalid_topology_is_rejected_with_safe_started_fallback(
    mutation,
    reason: str,
) -> None:
    topology = _topology()
    mutation(topology)
    event = _event(attributes={"topology": topology, "outcome": "unknown"})

    result = sanitize_run_event(event, RunHistorySettings())

    assert result.event is None
    assert result.reason == reason
    assert result.partial_started_envelope == {
        "schema_version": 1,
        "run_id": "run-privacy-boundary",
        "seq": 1,
        "occurred_at": "2026-08-23T08:00:00Z",
        "elapsed_ms": 12.5,
        "topology_id": "rag.query",
        "topology_revision": "rev-test",
        "type": "run.started",
    }
    assert "SENTINEL" not in repr(result)


def test_topology_json_byte_cap_rejects_whole_topology() -> None:
    topology = _topology()
    topology["nodes"] = [
        {
            "id": f"node-{index}",
            "label": "x" * 128,
            "group": "retrieve",
            "description": "y" * 128,
            "optional": False,
            "repeatable": False,
            "available": True,
            "attributes": {},
        }
        for index in range(20)
    ]

    result = sanitize_run_event(
        _event(attributes={"topology": topology}),
        RunHistorySettings(max_topology_json_bytes=4096),
    )

    assert result.event is None
    assert result.reason == "topology_json_too_large"
    assert result.partial_started_envelope is not None


def test_copy_failure_returns_safe_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_copy(_event: RunEvent) -> dict[str, Any]:
        raise RuntimeError("SENTINEL_COPY_FAILURE")

    monkeypatch.setattr("core.run_registry.run_event_dict", fail_copy)

    result = sanitize_run_event(_event(), RunHistorySettings())

    assert result.event is None
    assert result.reason == "event_copy_failed"
    assert "SENTINEL_COPY_FAILURE" not in repr(result)


@pytest.mark.parametrize(
    "attributes",
    [
        {"chunk_count": True},
        {"enabled": 1},
    ],
)
def test_allowlisted_attribute_types_do_not_confuse_booleans_and_numbers(
    attributes: dict[str, Any],
) -> None:
    result = sanitize_run_event(_event(attributes=attributes), RunHistorySettings())

    assert result.event is None
    assert result.reason == "attributes_invalid_type"


def test_real_cache_replay_started_topology_is_accepted() -> None:
    topology = build_cache_replay_topology()
    sequencer = RunEventSequencer(
        "run-cache-replay",
        topology_id=topology.id,
        topology_revision=topology.revision,
        monotonic=lambda: 100.0,
        utcnow=lambda: datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc),
    )
    event = sequencer.start_run(
        attributes={
            "executor": topology.executor,
            "topology": topology_dict(topology),
        }
    )

    result = sanitize_run_event(event, RunHistorySettings())

    assert result.reason is None
    assert result.event is not None
    assert result.event["attributes"]["topology"] == topology_dict(topology)
    assert result.event["attributes"]["executor"] == "cache_replay"


@pytest.mark.parametrize(
    "hostile_key",
    [
        "apiKey",
        "ａｐｉＫｅｙ",
        "api-key",
        "API_KEY",
        "authorization",
        "query",
        "prompt",
        "answer",
        "token",
        "text",
        "acl",
        "tenant",
        "dataset",
        "document",
        "chunk",
        "embedding",
        "scores",
    ],
)
def test_nested_sensitive_key_variants_are_rejected_at_any_depth(hostile_key: str) -> None:
    sentinel = "SENTINEL_NESTED_SECRET"
    event = _event(
        attributes={
            "reason": {
                "safe": [
                    {
                        "nested": {
                            hostile_key: sentinel,
                        }
                    }
                ]
            }
        }
    )

    result = sanitize_run_event(event, RunHistorySettings())

    assert result.event is None
    assert result.reason == "attributes_unknown_key"
    assert sentinel not in repr(result)


@pytest.mark.parametrize(
    ("error_update", "reason"),
    [
        ({"type": None}, "error_invalid_type"),
        ({"type": ""}, "error_invalid_type"),
        ({"type": 7}, "error_invalid_type"),
        ({"type": ["RuntimeError"]}, "error_invalid_type"),
        ({"type": "x" * 97}, "error_string_too_long"),
        ({"code": None}, "error_invalid_type"),
        ({"code": ""}, "error_invalid_type"),
        ({"code": 7}, "error_invalid_type"),
        ({"code": "x" * 97}, "error_string_too_long"),
        ({"recoverable": 1}, "error_invalid_type"),
        ({"category": "SENTINEL_ERROR_CATEGORY"}, "error_unknown_key"),
    ],
)
def test_malformed_defensive_error_copy_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    error_update: dict[str, Any],
    reason: str,
) -> None:
    event = _event(
        event_type="node.failed",
        error=EventError(type="RuntimeError", code="failed", recoverable=False),
    )
    payload = event.model_dump(mode="json", exclude_none=True)
    payload["error"].update(error_update)
    monkeypatch.setattr("core.run_registry.run_event_dict", lambda _event: payload)

    result = sanitize_run_event(event, RunHistorySettings())

    assert result.event is None
    assert result.reason == reason
    assert "SENTINEL_ERROR_CATEGORY" not in repr(result)


class _FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, *, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _registry_event(
    run_id: str,
    seq: int,
    event_type: str,
    *,
    elapsed_ms: float | None = None,
    node_id: str | None = None,
    attempt: int | None = None,
    attributes: dict[str, Any] | None = None,
    error: EventError | None = None,
    topology_revision: str = "rev-test",
) -> RunEvent:
    terminal_node_event = event_type in {
        "node.completed",
        "node.failed",
        "retry.completed",
        "retry.failed",
    }
    return RunEvent(
        run_id=run_id,
        seq=seq,
        occurred_at=datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
        + timedelta(milliseconds=elapsed_ms if elapsed_ms is not None else seq),
        elapsed_ms=float(seq if elapsed_ms is None else elapsed_ms),
        topology_id="rag.query",
        topology_revision=topology_revision,
        type=event_type,
        node_id=node_id,
        attempt=attempt,
        duration_ms=2.0 if terminal_node_event else None,
        attributes=attributes or {},
        error=error,
    )


def _started(run_id: str, *, seq: int = 1) -> RunEvent:
    return _registry_event(
        run_id,
        seq,
        "run.started",
        attributes={"executor": "sequential_stream", "topology": _topology()},
    )


def _registry(clock: _FakeClock, **settings_updates: Any) -> RunRegistry:
    return RunRegistry(
        RunHistorySettings(**settings_updates),
        RegistryIdentity(
            boot_id="boot-a",
            worker_id="worker-a",
            scope_key=b"s" * 32,
            cursor_key=b"c" * 32,
            tenant_scope_stability="installation",
        ),
        utcnow=clock.now,
    )


def test_registry_projects_lifecycle_rollups_routes_retries_and_terminal() -> None:
    clock = _FakeClock()
    registry = _registry(clock)
    sink = registry.bound_sink("scope-a", "fingerprint-a")
    events = [
        _started("run-a"),
        _registry_event("run-a", 2, "node.started", node_id="search", attempt=1),
        _registry_event(
            "run-a",
            3,
            "route.selected",
            node_id="search",
            attributes={"effective_route": "hybrid"},
        ),
        _registry_event(
            "run-a",
            4,
            "degraded",
            node_id="search",
            attributes={"reason": "fallback"},
        ),
        _registry_event(
            "run-a",
            5,
            "node.failed",
            node_id="search",
            attempt=1,
            error=EventError(
                type="TimeoutError",
                code="timeout",
                recoverable=True,
            ),
        ),
        _registry_event(
            "run-a",
            6,
            "retry.started",
            node_id="search",
            attempt=1,
            attributes={"reason": "recoverable"},
        ),
        _registry_event("run-a", 7, "retry.completed", node_id="search", attempt=1),
        _registry_event(
            "run-a",
            8,
            "run.completed",
            elapsed_ms=42.0,
            attributes={"outcome": "answered"},
        ),
    ]
    for event in events:
        sink(event)

    detail = registry.get_detail("scope-a", "run-a")

    assert detail is not None
    assert detail.summary.status == "completed"
    assert detail.summary.outcome == "answered"
    assert detail.summary.current_node_ids == ()
    assert detail.summary.failed_node_ids == ("search",)
    assert detail.summary.route == "hybrid"
    assert (detail.summary.degraded_count, detail.summary.retry_count) == (1, 1)
    assert detail.summary.query_fingerprint == "fingerprint-a"
    assert detail.topology["revision"] == "rev-test"
    assert [(item.node_id, item.attempt, item.status) for item in detail.node_rollup] == [
        ("search", 1, "failed"),
        ("search", 1, "retry_completed"),
    ]
    event_slice = registry.get_events("scope-a", "run-a", 0, 20)
    assert event_slice is not None
    assert [event["seq"] for event in event_slice.events] == list(range(1, 9))


def test_exact_duplicate_is_idempotent_but_conflict_gap_and_mismatch_mark_partial() -> None:
    clock = _FakeClock()
    registry = _registry(clock)
    sink = registry.bound_sink("scope-a")
    started = _started("run-integrity")
    sink(started)
    sink(started)
    sink(_registry_event("run-integrity", 3, "degraded", attributes={"reason": "gap"}))
    sink(_registry_event("run-integrity", 3, "degraded", attributes={"reason": "conflict"}))
    sink(
        _registry_event(
            "run-integrity",
            4,
            "degraded",
            attributes={"reason": "wrong-topology"},
            topology_revision="other",
        )
    )

    detail = registry.get_detail("scope-a", "run-integrity")

    assert detail is not None
    assert detail.summary.event_count == 2
    assert detail.summary.last_seq == 3
    assert detail.summary.event_integrity == "partial"
    assert detail.summary.degraded_count == 1


def test_terminal_is_immutable_and_late_event_is_ignored() -> None:
    clock = _FakeClock()
    registry = _registry(clock)
    sink = registry.bound_sink("scope-a")
    sink(_started("run-terminal"))
    sink(
        _registry_event(
            "run-terminal",
            2,
            "run.completed",
            attributes={"outcome": "abstained"},
        )
    )
    before = registry.get_detail("scope-a", "run-terminal")
    sink(_registry_event("run-terminal", 999, "degraded", attributes={"reason": "late"}))

    assert registry.get_detail("scope-a", "run-terminal") == before
    assert before is not None and before.summary.status == "completed"


def test_unseen_non_started_event_is_ignored_and_scope_is_checked_first() -> None:
    clock = _FakeClock()
    registry = _registry(clock)
    registry.bound_sink("scope-a")(_registry_event("run-unseen", 2, "degraded"))
    registry.bound_sink("scope-a")(_started("run-visible"))

    assert registry.get_detail("scope-a", "run-unseen") is None
    assert registry.get_detail("scope-b", "run-visible") is None
    assert registry.get_events("scope-b", "run-visible", 0, 10) is None


def test_active_capacity_drops_registry_copy_only_and_sink_is_failure_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    registry = _registry(clock, memory_max_active_runs=1)
    registry.bound_sink("scope-a")(_started("run-one"))
    registry.bound_sink("scope-a")(_started("run-two"))

    assert registry.get_detail("scope-a", "run-one") is not None
    assert registry.get_detail("scope-a", "run-two") is None
    assert registry.health_snapshot().memory.dropped_runs == 1

    def fail_accept(_context: BoundRunContext, _event: RunEvent) -> None:
        raise RuntimeError("SENTINEL_BOOM")

    monkeypatch.setattr(registry, "_accept", fail_accept)
    registry.bound_sink("scope-a")(_started("run-three"))
    assert registry.health_snapshot().sink_errors == 1


def test_per_run_window_keeps_started_plus_tail_and_reports_gap() -> None:
    clock = _FakeClock()
    registry = _registry(clock, memory_max_events_per_run=3)
    sink = registry.bound_sink("scope-a")
    sink(_started("run-window"))
    for seq in range(2, 7):
        sink(_registry_event("run-window", seq, "degraded", attributes={"reason": str(seq)}))

    detail = registry.get_detail("scope-a", "run-window")
    event_slice = registry.get_events("scope-a", "run-window", 0, 20)

    assert detail is not None and event_slice is not None
    assert [event["seq"] for event in event_slice.events] == [1, 5, 6]
    assert event_slice.history_state == "partial"
    assert event_slice.earliest_available_seq == 5
    assert detail.summary.event_integrity == "partial"


def test_terminal_lru_ttl_and_global_pressure_never_evict_active_summary() -> None:
    clock = _FakeClock()
    registry = _registry(
        clock,
        memory_max_recent_runs=1,
        memory_max_events_total=4,
        memory_terminal_ttl_s=60,
    )
    active_sink = registry.bound_sink("scope-a")
    active_sink(_started("run-active"))
    active_sink(_registry_event("run-active", 2, "degraded"))
    for run_id in ("run-old", "run-new"):
        sink = registry.bound_sink("scope-a")
        sink(_started(run_id))
        sink(_registry_event(run_id, 2, "run.completed"))

    assert registry.get_detail("scope-a", "run-active") is not None
    assert registry.get_detail("scope-a", "run-old") is None
    assert registry.get_detail("scope-a", "run-new") is not None
    clock.advance(seconds=61)
    registry.cleanup()
    assert registry.get_detail("scope-a", "run-new") is None
    assert registry.get_detail("scope-a", "run-active") is not None


def test_views_derive_active_recent_slow_errors_cancelled_and_stuck() -> None:
    clock = _FakeClock()
    registry = _registry(clock, stuck_after_s=5, slow_threshold_ms=3000)
    for run_id in ("active", "failed", "cancelled", "interrupted"):
        registry.bound_sink("scope-a")(_started(run_id))
    registry.bound_sink("scope-a")(_registry_event("failed", 2, "run.failed"))
    registry.bound_sink("scope-a")(_registry_event("cancelled", 2, "run.cancelled"))
    registry.recover_interrupted("scope-a", "interrupted", reason="worker_lost")
    registry.record_worker_heartbeat("worker-a", alive=True, at=clock.now())
    clock.advance(seconds=6)

    assert [item.run_id for item in registry.list_runs(RunListQuery("scope-a", view="active"))] == [
        "active"
    ]
    assert {item.run_id for item in registry.list_runs(RunListQuery("scope-a", view="errors"))} == {
        "failed",
        "interrupted",
    }
    assert [item.run_id for item in registry.list_runs(RunListQuery("scope-a", view="stuck"))] == [
        "active"
    ]
    assert [
        item.run_id
        for item in registry.list_runs(RunListQuery("scope-a", view="slow", slow_ms=3000))
    ] == ["active"]
    assert [
        item.run_id for item in registry.list_runs(RunListQuery("scope-a", statuses=("cancelled",)))
    ] == ["cancelled"]


def test_public_snapshots_are_frozen_and_node_lists_are_stable_and_bounded() -> None:
    clock = _FakeClock()
    registry = _registry(clock)
    sink = registry.bound_sink("scope-a")
    sink(_started("run-frozen"))
    for index in range(40):
        sink(
            _registry_event(
                "run-frozen",
                index + 2,
                "node.started",
                node_id=f"n-{index:02d}",
                attempt=1,
            )
        )

    detail = registry.get_detail("scope-a", "run-frozen")

    assert detail is not None
    assert detail.summary.current_node_ids == tuple(f"n-{index:02d}" for index in range(32))
    with pytest.raises(TypeError):
        detail.topology["revision"] = "changed"  # type: ignore[index]
    events = registry.get_events("scope-a", "run-frozen", 0, 1)
    assert events is not None
    with pytest.raises(TypeError):
        events.events[0]["seq"] = 99  # type: ignore[index]


def test_wait_for_change_wakes_and_persistence_gap_is_visible() -> None:
    clock = _FakeClock()
    offered: list[Any] = []
    registry = _registry(clock)
    registry.set_persistence_offer(lambda mutation: offered.append(mutation) or True)
    sink = registry.bound_sink("scope-a")
    sink(_started("run-wait"))
    version = registry.current_version("run-wait")
    result: list[int] = []
    waiter = Thread(
        target=lambda: result.append(registry.wait_for_change("run-wait", version, 1.0))
    )
    waiter.start()
    sink(_registry_event("run-wait", 2, "degraded"))
    waiter.join(timeout=2)
    registry.mark_persistence_gap("scope-a", "run-wait")

    assert result and result[0] > version
    assert len(offered) == 2
    detail = registry.get_detail("scope-a", "run-wait")
    assert detail is not None
    assert detail.summary.persistence_status == "partial"


def test_hydrate_terminal_history() -> None:
    clock = _FakeClock()
    source = _registry(clock)
    sink = source.bound_sink("scope-a")
    sink(_started("run-hydrated"))
    sink(_registry_event("run-hydrated", 2, "run.completed", elapsed_ms=10.0))
    detail = source.get_detail("scope-a", "run-hydrated")
    events = source.get_events("scope-a", "run-hydrated", 0, 20)
    assert detail is not None and events is not None

    target = _registry(clock)
    target.hydrate("scope-a", detail, events.events)
    assert target.get_detail("scope-a", "run-hydrated") == detail


def test_registry_sink_production_offer_hot_path_budget(tmp_path: Path) -> None:
    clock = _FakeClock()
    settings = RunHistorySettings(
        sqlite_path=str(tmp_path / "run-history.sqlite3"),
        writer_queue_capacity=4096,
        writer_batch_size=64,
        writer_flush_ms=100,
    )
    store = RunHistoryStore.open(settings, "boot-budget", "worker-budget", now=clock.now)
    identity = RegistryIdentity(
        boot_id="boot-budget",
        worker_id="worker-budget",
        scope_key=store.scope_key,
        cursor_key=store.cursor_key,
        tenant_scope_stability="installation",
    )
    registry = RunRegistry(
        settings,
        identity,
        utcnow=clock.now,
        persistence_offer=store.enqueue,
    )
    sink = registry.bound_sink("scope-a")
    samples: list[float] = []
    try:
        for index in range(1000):
            event = _started(f"budget-{index}")
            started_at = perf_counter()
            sink(event)
            samples.append((perf_counter() - started_at) * 1000)
            sink(_registry_event(event.run_id, 2, "run.completed"))
        samples.sort()
        p95_ms = samples[int(len(samples) * 0.95) - 1]
        p99_ms = samples[int(len(samples) * 0.99) - 1]
        assert p95_ms < 1.0
        assert p99_ms < 2.0
        assert store.flush_for_test(timeout_s=10)
        first = store.get_events("scope-a", "budget-0", 0, 10)
        last = store.get_events("scope-a", "budget-999", 0, 10)
        assert first is not None and last is not None
        assert [event["seq"] for event in first.events] == [1, 2]
        assert [event["seq"] for event in last.events] == [1, 2]
    finally:
        store.close(2000)


def test_duplicate_of_truncated_event_remains_idempotent() -> None:
    clock = _FakeClock()
    registry = _registry(clock, memory_max_events_per_run=2)
    sink = registry.bound_sink("scope-a")
    sink(_started("run-truncated-duplicate"))
    second = _registry_event(
        "run-truncated-duplicate",
        2,
        "degraded",
        attributes={"reason": "second"},
    )
    sink(second)
    sink(
        _registry_event(
            "run-truncated-duplicate",
            3,
            "degraded",
            attributes={"reason": "third"},
        )
    )
    before = registry.get_detail("scope-a", "run-truncated-duplicate")
    before_version = registry.current_version("run-truncated-duplicate")

    sink(second)
    assert registry.get_detail("scope-a", "run-truncated-duplicate") == before
    assert registry.current_version("run-truncated-duplicate") == before_version


def test_cross_scope_rejected_event_cannot_mutate_existing_run() -> None:
    clock = _FakeClock()
    registry = _registry(clock)
    registry.bound_sink("scope-a")(_started("run-scope-rejected"))
    before_detail = registry.get_detail("scope-a", "run-scope-rejected")
    before_health = registry.health_snapshot()

    registry.bound_sink("scope-b")(
        _registry_event(
            "run-scope-rejected",
            2,
            "degraded",
            attributes={"query": "SENTINEL_CROSS_SCOPE"},
        )
    )

    assert registry.get_detail("scope-a", "run-scope-rejected") == before_detail
    assert registry.health_snapshot() == before_health


def test_cross_scope_partial_started_cannot_mutate_existing_run() -> None:
    clock = _FakeClock()
    registry = _registry(clock)
    registry.bound_sink("scope-a")(_started("run-scope-partial-start"))
    before_detail = registry.get_detail("scope-a", "run-scope-partial-start")
    before_health = registry.health_snapshot()
    invalid_topology = _topology(revision="other-revision")

    registry.bound_sink("scope-b")(
        _registry_event(
            "run-scope-partial-start",
            1,
            "run.started",
            attributes={
                "executor": "sequential_stream",
                "topology": invalid_topology,
            },
        )
    )

    assert registry.get_detail("scope-a", "run-scope-partial-start") == before_detail
    assert registry.health_snapshot() == before_health


def test_out_of_order_gap_fill_is_retained_and_rebuilds_node_rollup() -> None:
    clock = _FakeClock()
    registry = _registry(clock)
    sink = registry.bound_sink("scope-a")
    sink(_started("run-gap-fill"))
    sink(
        _registry_event(
            "run-gap-fill",
            3,
            "node.completed",
            node_id="search",
            attempt=1,
        )
    )
    before = registry.get_detail("scope-a", "run-gap-fill")
    assert before is not None
    assert before.summary.earliest_available_seq == 3

    sink(
        _registry_event(
            "run-gap-fill",
            2,
            "node.started",
            node_id="search",
            attempt=1,
        )
    )

    detail = registry.get_detail("scope-a", "run-gap-fill")
    events = registry.get_events("scope-a", "run-gap-fill", 0, 10)
    assert detail is not None and events is not None
    assert [event["seq"] for event in events.events] == [1, 2, 3]
    assert detail.summary.last_seq == 3
    assert detail.summary.event_count == 3
    assert detail.summary.event_integrity == "partial"
    assert detail.summary.earliest_available_seq == 1
    assert detail.summary.current_node_ids == ()
    assert [
        (
            item.node_id,
            item.status,
            item.started_elapsed_ms,
            item.finished_elapsed_ms,
        )
        for item in detail.node_rollup
    ] == [("search", "completed", 2.0, 3.0)]


def test_out_of_order_fill_cannot_reopen_terminal_run() -> None:
    clock = _FakeClock()
    registry = _registry(clock)
    sink = registry.bound_sink("scope-a")
    sink(_started("run-terminal-fill"))
    sink(
        _registry_event(
            "run-terminal-fill",
            3,
            "run.completed",
            attributes={"outcome": "answered"},
        )
    )

    sink(
        _registry_event(
            "run-terminal-fill",
            2,
            "node.started",
            node_id="search",
            attempt=1,
        )
    )

    detail = registry.get_detail("scope-a", "run-terminal-fill")
    events = registry.get_events("scope-a", "run-terminal-fill", 0, 10)
    assert detail is not None and events is not None
    assert detail.summary.status == "completed"
    assert detail.summary.outcome == "answered"
    assert detail.summary.last_seq == 3
    assert detail.summary.event_count == 3
    assert detail.summary.current_node_ids == ()
    assert [event["seq"] for event in events.events] == [1, 2, 3]

    before = detail
    sink(_registry_event("run-terminal-fill", 4, "degraded"))
    assert registry.get_detail("scope-a", "run-terminal-fill") == before



def test_registry_internal_keyset_reads_150_same_timestamp_runs() -> None:
    clock = _FakeClock()
    registry = _registry(clock, memory_max_active_runs=256)
    sink = registry.bound_sink("scope-a")
    for index in range(150):
        sink(_started(f"run-{index:03}"))

    query = RunListQuery("scope-a", limit=101, as_of=clock.now() + timedelta(milliseconds=1))
    first = registry.list_runs(query)
    second = registry.list_runs(query, before=(first[-1].started_at, first[-1].run_id))

    assert len(first) == 101
    assert len(second) == 49
    assert len({item.run_id for item in [*first, *second]}) == 150



def test_registry_applies_as_of_before_internal_limit() -> None:
    clock = _FakeClock()
    registry = _registry(clock, memory_max_active_runs=256)
    sink = registry.bound_sink("scope-a")
    for index in range(50):
        sink(_started(f"visible-{index:03}"))
    for index in range(101):
        sink(
            _started(f"future-{index:03}").model_copy(
                update={"occurred_at": clock.now() + timedelta(seconds=1000)}
            )
        )

    page = registry.list_runs(
        RunListQuery("scope-a", limit=101, as_of=clock.now() + timedelta(seconds=1))
    )
    assert len(page) == 50
    assert all(item.run_id.startswith("visible-") for item in page)
