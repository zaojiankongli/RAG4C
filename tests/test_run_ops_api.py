from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from starlette.requests import Request

from config.settings import RunHistorySettings, TenantSettings
from core.run_history_store import (
    PersistenceMutation,
    RunHistoryReadError,
    RunHistoryStore,
    StoreHealth,
    StoredEventSlice,
    StoredRun,
)
from core.run_registry import (
    NodeRollup,
    RegistryHealth,
    RegistryIdentity,
    RegistryMemoryHealth,
    RunDetail,
    RunEventSlice,
    RunHistory,
    RunListQuery,
    RunSummary,
    derive_tenant_scope,
)
from server.app import app as application
from server.run_ops import (
    CursorCodec,
    CursorError,
    CursorPayload,
    RunDetailResponse,
    RunEventsResponse,
    RunHealthResponse,
    RunListFilters,
    RunListResponse,
    RunNotFoundError,
    RunOpsService,
    RunSummaryResponse,
    create_run_ops_router,
    require_operator_access,
    resolve_request_tenant,
)

NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)


def _request(host: str, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/runs",
            "raw_path": b"/api/runs",
            "query_string": b"",
            "headers": raw_headers,
            "client": (host, 43120),
            "server": ("testserver", 80),
        }
    )


def _history_settings(token: str | None = None) -> RunHistorySettings:
    return RunHistorySettings(
        ops_bearer_token=SecretStr(token) if token is not None else None,
    )


def _tenant_settings(default: str = "default") -> SimpleNamespace:
    return SimpleNamespace(tenant=TenantSettings(enforced=True, default_tenant=default))


@pytest.mark.parametrize("host", ["127.0.0.1", "127.42.7.9", "::1"])
def test_operator_access_allows_ipv4_and_ipv6_loopback_without_token(host: str) -> None:
    assert require_operator_access(_request(host), _history_settings()) is None


def test_operator_access_does_not_trust_x_forwarded_for() -> None:
    request = _request("10.0.0.2", {"X-Forwarded-For": "127.0.0.1"})
    with pytest.raises(HTTPException) as exc_info:
        require_operator_access(request, _history_settings())
    assert exc_info.value.status_code == 403
    assert exc_info.value.headers is None


def test_remote_operator_access_without_configured_token_is_forbidden() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_operator_access(_request("10.0.0.2"), _history_settings())
    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic ops-secret", "Bearer", "Bearer wrong-secret"],
)
def test_remote_operator_access_with_missing_or_invalid_token_is_challenged(
    authorization: str | None,
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}
    with pytest.raises(HTTPException) as exc_info:
        require_operator_access(_request("10.0.0.2", headers), _history_settings("ops-secret"))
    assert exc_info.value.status_code == 401
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


def test_remote_operator_access_uses_compare_digest_for_valid_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compared: list[tuple[bytes, bytes]] = []

    def audited_compare_digest(provided: bytes, configured: bytes) -> bool:
        compared.append((provided, configured))
        return provided == configured

    monkeypatch.setattr("server.run_ops.hmac.compare_digest", audited_compare_digest)
    request = _request("10.0.0.2", {"Authorization": "Bearer ops-secret"})
    assert require_operator_access(request, _history_settings("ops-secret")) is None
    assert compared == [(b"ops-secret", b"ops-secret")]


def test_local_tenant_resolution_uses_existing_default_resolution() -> None:
    assert resolve_request_tenant(_request("127.0.0.1"), None, _tenant_settings("local")) == (
        "local"
    )


def test_remote_tenant_resolution_requires_explicit_header() -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_request_tenant(_request("10.0.0.2"), "query-tenant", _tenant_settings())
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "tenant_required"


def test_remote_tenant_resolution_uses_header_and_ignores_forwarded_host() -> None:
    request = _request(
        "10.0.0.2",
        {"X-RAG4C-Tenant": "remote-tenant", "X-Forwarded-For": "127.0.0.1"},
    )
    assert resolve_request_tenant(request, None, _tenant_settings()) == "remote-tenant"


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.2"])
def test_tenant_header_query_mismatch_is_rejected(host: str) -> None:
    request = _request(host, {"X-RAG4C-Tenant": "header-tenant"})
    with pytest.raises(HTTPException) as exc_info:
        resolve_request_tenant(request, "query-tenant", _tenant_settings())
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "tenant_mismatch"


def _cursor_payload(**changes: Any) -> CursorPayload:
    payload: dict[str, Any] = {
        "v": 1,
        "tenant_scope": "scope-租户",
        "filter_hash": "filter-a",
        "as_of_us": 1_787_472_060_000_000,
        "last_started_at_us": 1_787_472_000_000_000,
        "last_run_id": "run-9",
        "exp": int(NOW.timestamp()) + 3600,
    }
    payload.update(changes)
    return CursorPayload.model_validate(payload)


def _decode_b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))



def _signed_cursor(key: bytes, payload: dict[str, Any], *, canonical: bool = True) -> str:
    raw = json.dumps(
        payload,
        sort_keys=canonical,
        separators=(",", ":") if canonical else None,
        ensure_ascii=False,
    ).encode("utf-8")
    signature = hmac.new(key, raw, hashlib.sha256).digest()
    return ".".join(
        base64.urlsafe_b64encode(part).rstrip(b"=").decode("ascii")
        for part in (raw, signature)
    )

def test_cursor_encoding_is_sorted_compact_utf8_and_hmac_sha256() -> None:
    key = b"cursor-test-key"
    token = CursorCodec(key).encode(_cursor_payload())
    encoded_payload, encoded_signature = token.split(".")
    expected_payload = (
        '{"as_of_us":1787472060000000,"exp":1787475600,'
        '"filter_hash":"filter-a","last_run_id":"run-9",'
        '"last_started_at_us":1787472000000000,"tenant_scope":"scope-租户","v":1}'
    ).encode("utf-8")
    assert _decode_b64url(encoded_payload) == expected_payload
    assert _decode_b64url(encoded_signature) == hmac.new(
        key, expected_payload, hashlib.sha256
    ).digest()


def test_cursor_round_trip_honors_3600_second_expiry() -> None:
    codec = CursorCodec(b"cursor-test-key")
    token = codec.encode(_cursor_payload())
    decoded = codec.decode(token, "scope-租户", "filter-a", NOW + timedelta(seconds=3599))
    assert decoded.last_run_id == "run-9"
    with pytest.raises(CursorError, match="cursor_expired") as exc_info:
        codec.decode(token, "scope-租户", "filter-a", NOW + timedelta(seconds=3600))
    assert exc_info.value.code == "cursor_expired"


def test_cursor_tampering_has_distinct_invalid_code() -> None:
    codec = CursorCodec(b"cursor-test-key")
    token = codec.encode(_cursor_payload())
    payload, signature = token.split(".")
    replacement = "A" if payload[-1] != "A" else "B"
    with pytest.raises(CursorError, match="cursor_invalid") as exc_info:
        codec.decode(f"{payload[:-1]}{replacement}.{signature}", "scope-租户", "filter-a", NOW)
    assert exc_info.value.code == "cursor_invalid"


@pytest.mark.parametrize(
    ("scope", "filter_hash", "code"),
    [
        ("other-scope", "filter-a", "cursor_scope_mismatch"),
        ("scope-租户", "filter-b", "cursor_filter_mismatch"),
    ],
)
def test_cursor_scope_and_filter_mismatches_have_distinct_codes(
    scope: str, filter_hash: str, code: str
) -> None:
    codec = CursorCodec(b"cursor-test-key")
    token = codec.encode(_cursor_payload())
    with pytest.raises(CursorError, match=code) as exc_info:
        codec.decode(token, scope, filter_hash, NOW)
    assert exc_info.value.code == code


def test_cursor_decode_requires_explicit_version() -> None:
    key = b"cursor-test-key"
    payload = _cursor_payload().model_dump()
    del payload["v"]

    with pytest.raises(CursorError, match="cursor_invalid"):
        CursorCodec(key).decode(_signed_cursor(key, payload), "scope-租户", "filter-a", NOW)


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_cursor_decode_rejects_coerced_version_types(version: Any) -> None:
    key = b"cursor-test-key"
    payload = {**_cursor_payload().model_dump(), "v": version}

    with pytest.raises(CursorError, match="cursor_invalid"):
        CursorCodec(key).decode(_signed_cursor(key, payload), "scope-租户", "filter-a", NOW)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("as_of_us", True),
        ("as_of_us", 1_787_472_060_000_000.0),
        ("as_of_us", "1787472060000000"),
        ("last_started_at_us", True),
        ("last_started_at_us", 1_787_472_000_000_000.0),
        ("last_started_at_us", "1787472000000000"),
        ("exp", True),
        ("exp", 1_787_475_600.0),
        ("exp", "1787475600"),
    ],
)
def test_cursor_decode_rejects_coerced_integer_types(field: str, value: Any) -> None:
    key = b"cursor-test-key"
    payload = {**_cursor_payload().model_dump(), field: value}

    with pytest.raises(CursorError, match="cursor_invalid"):
        CursorCodec(key).decode(_signed_cursor(key, payload), "scope-租户", "filter-a", NOW)


def test_cursor_rejects_canonical_signed_extra_field() -> None:
    key = b"cursor-test-key"
    payload = {**_cursor_payload().model_dump(), "unexpected": True}

    with pytest.raises(CursorError, match="cursor_invalid"):
        CursorCodec(key).decode(_signed_cursor(key, payload), "scope-租户", "filter-a", NOW)


def test_cursor_rejects_signed_noncanonical_json_with_valid_fields() -> None:
    key = b"cursor-test-key"
    token = _signed_cursor(key, _cursor_payload().model_dump(), canonical=False)

    with pytest.raises(CursorError, match="cursor_invalid"):
        CursorCodec(key).decode(token, "scope-租户", "filter-a", NOW)


def test_cursor_ttl_boundary_is_inclusive() -> None:
    codec = CursorCodec(b"cursor-test-key")
    as_of_us = _cursor_payload().as_of_us
    payload = _cursor_payload(exp=as_of_us // 1_000_000 + codec.ttl_s)

    token = codec.encode(payload)
    assert codec.decode(token, "scope-租户", "filter-a", NOW).exp == payload.exp


def test_cursor_encode_rejects_exp_beyond_as_of_plus_ttl() -> None:
    codec = CursorCodec(b"cursor-test-key")
    as_of_us = _cursor_payload().as_of_us
    payload = _cursor_payload(exp=as_of_us // 1_000_000 + codec.ttl_s + 1)

    with pytest.raises(CursorError, match="cursor_invalid") as exc_info:
        codec.encode(payload)
    assert exc_info.value.code == "cursor_invalid"


def test_cursor_decode_rejects_signed_exp_beyond_as_of_plus_ttl() -> None:
    key = b"cursor-test-key"
    codec = CursorCodec(key)
    payload = _cursor_payload().model_dump()
    payload["exp"] = payload["as_of_us"] // 1_000_000 + codec.ttl_s + 1

    with pytest.raises(CursorError, match="cursor_invalid") as exc_info:
        codec.decode(_signed_cursor(key, payload), "scope-租户", "filter-a", NOW)
    assert exc_info.value.code == "cursor_invalid"




def _summary() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": "run-9",
        "status": "completed",
        "outcome": "answered",
        "started_at": NOW,
        "updated_at": NOW + timedelta(seconds=3, milliseconds=100),
        "finished_at": NOW + timedelta(seconds=3, milliseconds=100),
        "elapsed_ms": 3100.0,
        "boot_id": "boot-1",
        "worker_id": "worker-1",
        "topology_id": "rag.query",
        "topology_revision": "sha256:abc",
        "executor": "sequential_stream",
        "last_seq": 3,
        "event_count": 3,
        "earliest_available_seq": 1,
        "current_node_ids": [],
        "failed_node_ids": [],
        "route": "hybrid",
        "degraded_count": 0,
        "retry_count": 0,
        "attention": [],
        "event_integrity": "complete",
        "persistence_status": "durable",
        "interruption_reason": None,
        "query_fingerprint": "fingerprint-1",
    }


def _topology() -> dict[str, Any]:
    return {
        "id": "rag.query",
        "revision": "sha256:abc",
        "executor": "sequential_stream",
        "nodes": [
            {
                "id": "receive",
                "label": "Receive",
                "group": "input",
                "description": "Accept input",
                "optional": False,
                "repeatable": False,
                "available": True,
                "attributes": {},
            }
        ],
        "edges": [],
    }


def _event() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": "run-9",
        "seq": 1,
        "occurred_at": NOW,
        "elapsed_ms": 0.0,
        "topology_id": "rag.query",
        "topology_revision": "sha256:abc",
        "type": "run.started",
        "node_id": None,
        "attempt": None,
        "duration_ms": None,
        "attributes": {"executor": "sequential_stream"},
        "error": None,
    }


def _health() -> dict[str, Any]:
    return {
        "status": "ok",
        "enabled": True,
        "boot_id": "boot-1",
        "worker_id": "worker-1",
        "memory": {
            "active_runs": 2,
            "recent_runs": 138,
            "event_count": 6240,
            "dropped_runs": 0,
            "dropped_events": 0,
        },
        "persistence": {
            "enabled": True,
            "state": "ready",
            "database": "run-history.sqlite3",
            "wal": True,
            "writer_queue_depth": 0,
            "last_commit_at": NOW + timedelta(seconds=3, milliseconds=100),
            "commit_lag_ms": 12.0,
            "dropped_mutations": 0,
            "quick_check": "ok",
        },
        "heartbeat": {
            "interval_s": 5,
            "stale_after_s": 30,
            "last_heartbeat_at": NOW + timedelta(seconds=5),
        },
        "retention": {"days": 30, "max_runs": 100000, "memory_terminal_ttl_s": 21600},
        "scope_stability": "installation",
    }


def _list() -> dict[str, Any]:
    return {
        "items": [_summary()],
        "next_cursor": None,
        "as_of": NOW,
        "source": "memory+sqlite",
        "history_state": "complete",
        "retention": {"days": 30, "max_runs": 100000},
    }


def _detail() -> dict[str, Any]:
    return {
        "summary": _summary(),
        "topology": _topology(),
        "node_rollup": [
            {
                "node_id": "receive",
                "attempt": 1,
                "status": "completed",
                "started_elapsed_ms": 0.0,
                "finished_elapsed_ms": 1.0,
                "duration_ms": 1.0,
                "degraded_reason": None,
                "retry_reason": None,
                "error_type": None,
                "error_code": None,
            }
        ],
        "history": {
            "event_integrity": "complete",
            "earliest_available_seq": 1,
            "last_seq": 3,
            "persistence_status": "durable",
        },
    }


def _events(event: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "run_id": "run-9",
        "events": [event or _event()],
        "after_seq": 1,
        "latest_seq": 1,
        "terminal": False,
        "timed_out": True,
        "history_state": "complete",
        "earliest_available_seq": 1,
        "persistence_status": "durable",
        "retry_after_ms": 500,
    }


def test_response_models_freeze_schema_version_fields_and_utc_rfc3339() -> None:
    health = RunHealthResponse.model_validate(_health())
    listed = RunListResponse.model_validate(_list())
    detail = RunDetailResponse.model_validate(_detail())
    events = RunEventsResponse.model_validate(_events())
    for response in (health, listed, detail, events):
        assert response.schema_version == 1
    assert '"as_of":"2026-08-23T08:00:00.000Z"' in listed.model_dump_json()
    assert '"updated_at":"2026-08-23T08:00:03.100Z"' in detail.model_dump_json()
    assert '"occurred_at":"2026-08-23T08:00:00.000Z"' in events.model_dump_json()


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (RunHealthResponse, _health()),
        (RunListResponse, _list()),
        (RunDetailResponse, _detail()),
        (RunEventsResponse, _events()),
    ],
)
def test_response_models_forbid_extra_fields(model: type[Any], payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "tenant_scope": "must-not-leak"})


def test_response_models_reject_nan_and_nested_sensitive_fields() -> None:
    listed = _list()
    listed["items"][0]["elapsed_ms"] = float("nan")
    with pytest.raises(ValidationError):
        RunListResponse.model_validate(listed)

    event = _event()
    event["attributes"] = {"query": "raw secret"}
    with pytest.raises(ValidationError):
        RunEventsResponse.model_validate(_events(event))

    event = _event()
    event["error"] = {
        "type": "RuntimeError",
        "code": "failed",
        "recoverable": False,
        "message": "raw exception",
    }
    with pytest.raises(ValidationError):
        RunEventsResponse.model_validate(_events(event))


def _schema_property_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get("properties"), dict):
            names.update(value["properties"])
        for nested in value.values():
            names.update(_schema_property_names(nested))
    elif isinstance(value, list):
        for nested in value:
            names.update(_schema_property_names(nested))
    return names


def test_response_model_schemas_do_not_advertise_sensitive_fields() -> None:
    names: set[str] = set()
    for model in (RunHealthResponse, RunListResponse, RunDetailResponse, RunEventsResponse):
        names.update(_schema_property_names(model.model_json_schema()))
    assert names.isdisjoint(
        {
            "tenant_scope",
            "tenant_id",
            "acl",
            "dataset_id",
            "query",
            "answer",
            "message",
            "ops_bearer_token",
            "fingerprint_secret",
            "authorization",
            "cookie",
        }
    )


def test_response_serialization_schema_forbids_extras_and_marks_rfc3339_dates() -> None:
    list_schema = RunListResponse.model_json_schema(mode="serialization")
    summary_schema = list_schema["$defs"]["RunSummaryResponse"]

    assert summary_schema["additionalProperties"] is False
    assert "run_id" in summary_schema["properties"]
    assert list_schema["properties"]["as_of"]["format"] == "date-time"
    assert summary_schema["properties"]["started_at"]["format"] == "date-time"


def test_response_summary_omits_unconfigured_fingerprint_without_weakening_schema() -> None:
    payload = _summary()
    payload["query_fingerprint"] = None
    summary = RunSummaryResponse.model_validate(payload)

    assert "query_fingerprint" not in summary.model_dump(mode="json")
    schema = RunSummaryResponse.model_json_schema(mode="serialization")
    assert schema["additionalProperties"] is False
    assert "run_id" in schema["properties"]


def test_response_models_validate_frozen_registry_contracts() -> None:
    summary_payload = _summary()
    summary_payload["current_node_ids"] = tuple(summary_payload["current_node_ids"])
    summary_payload["failed_node_ids"] = tuple(summary_payload["failed_node_ids"])
    summary_payload["attention"] = tuple(summary_payload["attention"])
    summary = RunSummary(**summary_payload)
    rollup = NodeRollup(
        node_id="receive",
        attempt=1,
        status="completed",
        started_elapsed_ms=0.0,
        finished_elapsed_ms=1.0,
        duration_ms=1.0,
        degraded_reason=None,
        retry_reason=None,
        error_type=None,
        error_code=None,
    )
    history = RunHistory(
        event_integrity="complete",
        earliest_available_seq=1,
        last_seq=3,
        persistence_status="durable",
    )
    detail = RunDetail(
        schema_version=1,
        summary=summary,
        topology=MappingProxyType(_topology()),
        node_rollup=(rollup,),
        history=history,
    )
    event_slice = RunEventSlice(
        schema_version=1,
        run_id="run-9",
        events=(MappingProxyType(_event()),),
        after_seq=1,
        latest_seq=1,
        terminal=False,
        timed_out=False,
        history_state="complete",
        earliest_available_seq=1,
        persistence_status="durable",
    )

    assert RunDetailResponse.model_validate(detail).summary.run_id == "run-9"
    assert RunEventsResponse.model_validate(event_slice).events[0].seq == 1


def test_run_not_found_error_has_one_scope_opaque_contract() -> None:
    unknown = RunNotFoundError()
    cross_scope = RunNotFoundError()
    assert unknown.status_code == cross_scope.status_code == 404
    assert unknown.detail == cross_scope.detail == {
        "code": "run_not_found",
        "message": "运行不存在或不属于当前作用域",
    }


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _run_summary(
    run_id: str,
    seconds: int,
    *,
    status: str = "completed",
    updated_seconds: int | None = None,
    elapsed_ms: float = 1000.0,
    fingerprint: str | None = None,
) -> RunSummary:
    payload = _summary()
    started = NOW + timedelta(seconds=seconds)
    updated = NOW + timedelta(seconds=seconds if updated_seconds is None else updated_seconds)
    payload.update(
        run_id=run_id,
        status=status,
        started_at=started,
        updated_at=updated,
        finished_at=None if status == "running" else updated,
        elapsed_ms=elapsed_ms,
        query_fingerprint=fingerprint,
        persistence_status="pending",
    )
    return RunSummary(**payload)


def _event_for(run_id: str, seq: int, event_type: str, elapsed_ms: float) -> dict[str, Any]:
    payload = _event()
    payload.update(
        run_id=run_id,
        seq=seq,
        type=event_type,
        elapsed_ms=elapsed_ms,
        occurred_at=NOW + timedelta(milliseconds=elapsed_ms),
        node_id=None if event_type.startswith("run.") else "receive",
        attempt=None if event_type.startswith("run.") else 1,
    )
    return payload


class _FakeRegistry:
    def __init__(self, summaries: list[RunSummary] | None = None) -> None:
        self.summaries = list(summaries or [])
        self.details: dict[str, RunDetail] = {}
        self.queries: list[RunListQuery] = []
        self.event_slices: dict[str, RunEventSlice] = {}
        self._versions: dict[str, int] = {}
        self._change = threading.Event()
        self.wait_calls = 0
        self.event_call_order: list[str] = []

    def health_snapshot(self) -> RegistryHealth:
        return RegistryHealth(
            schema_version=1,
            status="ok",
            enabled=True,
            boot_id="boot-a",
            worker_id="worker-a",
            memory=RegistryMemoryHealth(1, 2, 3, 0, 0),
            sink_errors=0,
            rejected_events=0,
        )

    def list_runs(
        self, query: RunListQuery, *, before: tuple[datetime, str] | None = None
    ) -> list[RunSummary]:
        self.queries.append(query)
        values = [item for item in self.summaries if item.started_at <= (query.as_of or NOW)]
        if query.statuses:
            values = [item for item in values if item.status in query.statuses]
        if query.view == "active":
            values = [item for item in values if item.status == "running"]
        elif query.view == "errors":
            values = [item for item in values if item.status in {"failed", "interrupted"}]
        elif query.view == "slow":
            values = [item for item in values if item.elapsed_ms >= (query.slow_ms or 30000)]
        elif query.view == "stuck":
            values = [item for item in values if "stuck" in item.attention]
        if query.started_after is not None:
            values = [item for item in values if item.started_at >= query.started_after]
        if query.started_before is not None:
            values = [item for item in values if item.started_at < query.started_before]
        if query.fingerprint is not None:
            values = [item for item in values if item.query_fingerprint == query.fingerprint]
        if before is not None:
            values = [item for item in values if (item.started_at, item.run_id) < before]
        return sorted(values, key=lambda item: (item.started_at, item.run_id), reverse=True)[
            : query.limit
        ]

    def get_detail(self, tenant_scope: str, run_id: str) -> RunDetail | None:
        return self.details.get(run_id)

    def get_events(
        self, tenant_scope: str, run_id: str, after_seq: int, limit: int
    ) -> RunEventSlice | None:
        self.event_call_order.append(f"scoped:{tenant_scope}")
        value = self.event_slices.get(run_id)
        if value is None:
            return None
        events = tuple(event for event in value.events if event["seq"] > after_seq)[:limit]
        return replace(
            value,
            events=events,
            after_seq=events[-1]["seq"] if events else after_seq,
        )

    def current_version(self, run_id: str) -> int:
        self.event_call_order.append("version")
        return self._versions.get(run_id, 0)

    def wait_for_change(self, run_id: str, version: int, timeout_s: float) -> int:
        self.wait_calls += 1
        self._change.wait(timeout_s)
        self._change.clear()
        return self._versions.get(run_id, 0)

    def publish(self, run_id: str, event_slice: RunEventSlice) -> None:
        self.event_slices[run_id] = event_slice
        self._versions[run_id] = self._versions.get(run_id, 0) + 1
        self._change.set()


class _FakeStore:
    def __init__(self, summaries: list[RunSummary] | None = None) -> None:
        self.summaries = list(summaries or [])
        self.runs: dict[str, StoredRun] = {}
        self.events: dict[str, tuple[dict[str, Any], ...]] = {}
        self.list_scopes: list[str] = []
        self.detail_scopes: list[str] = []
        self.event_scopes: list[str] = []
        self.watermark_calls = 0

    def health_snapshot(self) -> StoreHealth:
        return StoreHealth(
            status="ok",
            write_enabled=True,
            reason=None,
            database_filename="run-history.sqlite3",
            writer_queue_depth=0,
            durable_run_count=len(self.summaries),
            durable_cache_entries=0,
            state="ready",
            quick_check="ok",
            recovery_action=None,
            last_commit_at=NOW,
            commit_lag_ms=12.5,
            dropped_mutations=3,
            last_heartbeat_at=NOW + timedelta(seconds=1),
        )

    def list_runs(
        self,
        tenant_scope: str,
        query: RunListQuery,
        *,
        before: tuple[datetime, str] | None = None,
    ) -> list[RunSummary]:
        self.list_scopes.append(tenant_scope)
        values = [item for item in self.summaries if item.started_at <= (query.as_of or NOW)]
        if before is not None:
            values = [item for item in values if (item.started_at, item.run_id) < before]
        return sorted(values, key=lambda item: (item.started_at, item.run_id), reverse=True)[
            : query.limit
        ]

    def get_run(self, tenant_scope: str, run_id: str) -> StoredRun | None:
        self.detail_scopes.append(tenant_scope)
        return self.runs.get(run_id)

    def get_events(
        self, tenant_scope: str, run_id: str, after_seq: int, limit: int
    ) -> StoredEventSlice | None:
        self.event_scopes.append(tenant_scope)
        stored = self.runs.get(run_id)
        if stored is None:
            return None
        values = tuple(event for event in self.events.get(run_id, ()) if event["seq"] > after_seq)[
            :limit
        ]
        summary = stored.summary
        return StoredEventSlice(
            run_id=run_id,
            events=values,
            after_seq=values[-1]["seq"] if values else after_seq,
            latest_seq=summary.last_seq,
            terminal=summary.status != "running",
            earliest_available_seq=summary.earliest_available_seq,
            persistence_status="durable",
        )

    def durable_watermark(self, tenant_scope: str, run_id: str) -> int:
        self.watermark_calls += 1
        stored = self.runs.get(run_id)
        return 0 if stored is None else stored.summary.last_seq


def _service(
    registry: _FakeRegistry,
    store: _FakeStore | None,
    *,
    clock: _Clock | None = None,
    settings: RunHistorySettings | None = None,
) -> RunOpsService:
    identity = RegistryIdentity("boot-a", "worker-a", b"s" * 32, b"c" * 32, "installation")
    return RunOpsService(
        registry,
        store,
        identity,
        settings or RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        clock or _Clock(),
    )


def _client(service: RunOpsService) -> TestClient:
    app = FastAPI()
    app.state.run_ops_runtime = SimpleNamespace(service=service)
    app.include_router(create_run_ops_router())
    return TestClient(app)


def test_openapi_has_only_approved_run_ops_routes_and_no_internal_scope_or_secrets() -> None:
    schema = TestClient(application).get("/openapi.json").json()

    assert {path for path in schema["paths"] if path.startswith("/api/runs")} == {
        "/api/runs/health",
        "/api/runs",
        "/api/runs/{run_id}",
        "/api/runs/{run_id}/events",
    }
    blob = json.dumps(schema, ensure_ascii=False).lower()
    assert "tenant_scope" not in blob
    assert "ops_bearer_token" not in blob
    assert "fingerprint_secret" not in blob


_OPS_HEADERS = {
    "Authorization": "Bearer ops-secret",
    "X-RAG4C-Tenant": "tenant-a",
}


def test_run_health_maps_registry_store_and_fixed_retention() -> None:
    response = _client(_service(_FakeRegistry(), _FakeStore())).get(
        "/api/runs/health", headers=_OPS_HEADERS
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["memory"] == {
        "active_runs": 1,
        "recent_runs": 2,
        "event_count": 3,
        "dropped_runs": 0,
        "dropped_events": 0,
    }
    assert payload["persistence"]["state"] == "ready"
    assert payload["persistence"]["last_commit_at"] == "2026-08-23T08:00:00.000Z"
    assert payload["persistence"]["commit_lag_ms"] == 12.5
    assert payload["persistence"]["dropped_mutations"] == 3
    assert payload["heartbeat"]["last_heartbeat_at"] == "2026-08-23T08:00:01.000Z"
    assert payload["retention"] == {
        "days": 30,
        "max_runs": 100000,
        "memory_terminal_ttl_s": 21600,
    }



def test_run_health_api_uses_actual_store_commit_drop_and_heartbeat_telemetry(
    tmp_path,
) -> None:
    clock = _Clock(NOW)
    settings = RunHistorySettings(
        sqlite_path=str(tmp_path / "qa-run-history.sqlite3"),
        writer_queue_capacity=8,
        writer_batch_size=1,
        writer_flush_ms=1,
        ops_bearer_token=SecretStr("ops-secret"),
    )
    store = RunHistoryStore.open(settings, "boot-real", "worker-real", now=clock)
    identity = RegistryIdentity(
        "boot-real", "worker-real", store.scope_key, store.cursor_key, "installation"
    )
    service = RunOpsService(_FakeRegistry(), store, identity, settings, clock)
    try:
        summary = _run_summary("run-health-real", 0, status="running", updated_seconds=0)
        event = _event_for("run-health-real", 1, "run.started", 0.0)
        event["attributes"] = {"executor": "sequential_stream"}
        event["occurred_at"] = NOW.isoformat().replace("+00:00", "Z")
        mutation = PersistenceMutation.from_values("scope-a", summary, _topology(), event)
        clock.now = NOW + timedelta(seconds=2)
        assert store.enqueue(mutation)
        assert store.flush_for_test(timeout_s=3)
        heartbeat = NOW + timedelta(seconds=3)
        store.mark_heartbeat(heartbeat)

        response = _client(service).get("/api/runs/health", headers=_OPS_HEADERS)

        assert response.status_code == 200
        payload = response.json()
        assert payload["persistence"]["last_commit_at"] == "2026-08-23T08:00:02.000Z"
        assert payload["persistence"]["commit_lag_ms"] == pytest.approx(2000.0)
        assert payload["persistence"]["dropped_mutations"] == 0
        assert payload["heartbeat"]["last_heartbeat_at"] == "2026-08-23T08:00:03.000Z"
    finally:
        store.close(2000)


def test_recent_endpoint_freezes_as_of_and_keyset_dedupes_memory_over_sqlite() -> None:
    clock = _Clock(NOW + timedelta(seconds=10))
    memory = _FakeRegistry([_run_summary("run-4", 4), _run_summary("run-2", 2)])
    durable_old = replace(_run_summary("run-4", 4), updated_at=NOW, persistence_status="durable")
    store = _FakeStore([durable_old, _run_summary("run-3", 3), _run_summary("run-1", 1)])
    client = _client(_service(memory, store, clock=clock))

    first = client.get("/api/runs?view=recent&limit=2", headers=_OPS_HEADERS).json()
    memory.summaries.append(_run_summary("run-new", 20))
    second = client.get(
        f"/api/runs?view=recent&limit=2&cursor={first['next_cursor']}",
        headers=_OPS_HEADERS,
    ).json()

    assert [item["run_id"] for item in first["items"] + second["items"]] == [
        "run-4",
        "run-3",
        "run-2",
        "run-1",
    ]
    assert first["items"][0]["persistence_status"] == "pending"
    assert second["as_of"] == first["as_of"]
    decoded = CursorCodec(b"c" * 32).decode(
        first["next_cursor"],
        memory.queries[0].tenant_scope,
        RunListFilters(view="recent", limit=2).filter_hash(),
        NOW,
    )
    assert decoded.exp == decoded.as_of_us // 1_000_000 + 3600
    assert all(scope == memory.queries[0].tenant_scope for scope in store.list_scopes)


@pytest.mark.parametrize(
    ("view", "statuses"),
    [
        ("active", ()),
        ("recent", ("cancelled",)),
        ("slow", ()),
        ("errors", ()),
        ("stuck", ()),
    ],
)
def test_list_filters_forward_views_and_repeated_status_without_tenant_in_hash(
    view: str, statuses: tuple[str, ...]
) -> None:
    registry = _FakeRegistry()
    service = _service(registry, None)
    first = RunListFilters(view=view, statuses=statuses, slow_ms=45000, limit=7)
    second = replace(first, cursor="opaque")

    asyncio.run(service.list_runs("opaque-scope", first))

    query = registry.queries[-1]
    assert (query.tenant_scope, query.view, query.statuses, query.slow_ms) == (
        "opaque-scope",
        view,
        tuple(sorted(statuses)),
        45000,
    )
    assert first.filter_hash() == second.filter_hash()


def test_sqlite_only_detail_builds_topology_rollup_and_history() -> None:
    run_id = "run-sqlite"
    summary = replace(
        _run_summary(run_id, 0),
        last_seq=3,
        event_count=3,
        persistence_status="durable",
    )
    store = _FakeStore([summary])
    store.runs[run_id] = StoredRun(summary=summary, topology=MappingProxyType(_topology()))
    store.events[run_id] = (
        _event_for(run_id, 1, "run.started", 0.0),
        _event_for(run_id, 2, "node.started", 10.0),
        {**_event_for(run_id, 3, "node.completed", 25.0), "duration_ms": 15.0},
    )

    response = _client(_service(_FakeRegistry(), store)).get(
        f"/api/runs/{run_id}", headers=_OPS_HEADERS
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["topology"]["id"] == "rag.query"
    assert payload["node_rollup"] == [
        {
            "node_id": "receive",
            "attempt": 1,
            "status": "completed",
            "started_elapsed_ms": 10.0,
            "finished_elapsed_ms": 25.0,
            "duration_ms": 15.0,
            "degraded_reason": None,
            "retry_reason": None,
            "error_type": None,
            "error_code": None,
        }
    ]
    assert payload["history"] == {
        "event_integrity": "complete",
        "earliest_available_seq": 1,
        "last_seq": 3,
        "persistence_status": "durable",
    }
    assert store.detail_scopes and len(set(store.detail_scopes)) == 1


def test_detail_unknown_and_cross_scope_share_scope_opaque_404() -> None:
    response = _client(_service(_FakeRegistry(), _FakeStore())).get(
        "/api/runs/missing", headers=_OPS_HEADERS
    )
    assert response.status_code == 404
    assert response.json()["detail"] == RunNotFoundError().detail


def test_missing_runtime_maps_to_safe_503_without_exception_text() -> None:
    app = FastAPI()
    app.include_router(create_run_ops_router())
    response = TestClient(app).get("/api/runs/health")
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "run_ops_unavailable",
        "message": "运行历史暂不可用",
    }



def _slice(
    run_id: str,
    events: tuple[dict[str, Any], ...],
    *,
    latest_seq: int,
    terminal: bool = False,
    earliest: int = 1,
    integrity: str = "complete",
    persistence: str = "pending",
) -> RunEventSlice:
    return RunEventSlice(
        schema_version=1,
        run_id=run_id,
        events=events,
        after_seq=events[-1]["seq"] if events else 0,
        latest_seq=latest_seq,
        terminal=terminal,
        timed_out=False,
        history_state=integrity,
        earliest_available_seq=earliest,
        persistence_status=persistence,
    )


def test_events_merge_sqlite_gap_fill_and_memory_duplicate_wins() -> None:
    run_id = "run-merge"
    memory = _FakeRegistry()
    memory_event = {**_event_for(run_id, 3, "node.completed", 30.0), "attributes": {}}
    memory.event_slices[run_id] = _slice(run_id, (memory_event,), latest_seq=3)
    summary = replace(
        _run_summary(run_id, 0, status="running"),
        last_seq=3,
        event_count=3,
        persistence_status="durable",
    )
    store = _FakeStore([summary])
    store.runs[run_id] = StoredRun(summary=summary, topology=MappingProxyType(_topology()))
    durable_duplicate = {**memory_event, "attributes": {"reason": "older"}}
    store.events[run_id] = (
        _event_for(run_id, 1, "run.started", 0.0),
        _event_for(run_id, 2, "node.started", 10.0),
        durable_duplicate,
    )

    response = _client(_service(memory, store)).get(
        f"/api/runs/{run_id}/events?after_seq=0&limit=3", headers=_OPS_HEADERS
    )

    assert response.status_code == 200
    payload = response.json()
    assert [event["seq"] for event in payload["events"]] == [1, 2, 3]
    assert payload["events"][2]["attributes"] == {}
    assert payload["history_state"] == "complete"
    assert store.event_scopes and len(set(store.event_scopes)) == 1


def test_irrecoverable_event_gap_returns_409() -> None:
    run_id = "run-gap"
    registry = _FakeRegistry()
    registry.event_slices[run_id] = _slice(
        run_id,
        (_event_for(run_id, 18, "node.started", 18.0),),
        latest_seq=42,
        earliest=18,
        integrity="partial",
    )

    response = _client(_service(registry, None)).get(
        f"/api/runs/{run_id}/events?after_seq=3", headers=_OPS_HEADERS
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "run_event_gap",
        "earliest_available_seq": 18,
        "latest_seq": 42,
    }


def test_event_terminal_returns_immediately_without_registry_wait() -> None:
    run_id = "run-terminal"
    registry = _FakeRegistry()
    registry.event_slices[run_id] = _slice(
        run_id, (), latest_seq=4, terminal=True, persistence="durable"
    )

    response = _client(_service(registry, None)).get(
        f"/api/runs/{run_id}/events?after_seq=4&wait_ms=25000", headers=_OPS_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["terminal"] is True
    assert response.json()["timed_out"] is False
    assert registry.wait_calls == 0


def test_same_worker_long_poll_wait_runs_off_event_loop_and_wakes() -> None:
    run_id = "run-live"
    registry = _FakeRegistry()
    registry.event_slices[run_id] = _slice(run_id, (), latest_seq=2)
    service = _service(registry, None)

    async def scenario() -> RunEventsResponse:
        pending = asyncio.create_task(service.get_events("scope-a", run_id, 2, 200, 1000))
        await asyncio.sleep(0.05)
        registry.publish(
            run_id,
            _slice(run_id, (_event_for(run_id, 3, "node.started", 30.0),), latest_seq=3),
        )
        return await asyncio.wait_for(pending, timeout=0.5)

    result = asyncio.run(scenario())
    assert [event.seq for event in result.events] == [3]
    assert result.timed_out is False
    assert registry.wait_calls == 1


def test_cross_worker_long_poll_polls_durable_store_at_250ms() -> None:
    run_id = "run-cross-worker"
    summary = replace(
        _run_summary(run_id, 0, status="running"),
        last_seq=2,
        event_count=2,
        persistence_status="durable",
    )
    store = _FakeStore([summary])
    store.runs[run_id] = StoredRun(summary=summary, topology=MappingProxyType(_topology()))
    store.events[run_id] = ()
    service = _service(_FakeRegistry(), store)

    def publish() -> None:
        time.sleep(0.08)
        store.events[run_id] = (_event_for(run_id, 3, "node.started", 30.0),)
        store.runs[run_id] = StoredRun(
            summary=replace(summary, last_seq=3, event_count=3),
            topology=MappingProxyType(_topology()),
        )

    thread = threading.Thread(target=publish)
    thread.start()
    started = time.monotonic()
    result = asyncio.run(service.get_events("scope-a", run_id, 2, 200, 1000))
    thread.join()

    assert [event.seq for event in result.events] == [3]
    assert time.monotonic() - started >= 0.20
    assert store.watermark_calls >= 1


def test_long_poll_admission_limit_returns_429_and_retry_after() -> None:
    run_id = "run-cap"
    registry = _FakeRegistry()
    registry.event_slices[run_id] = _slice(run_id, (), latest_seq=1)
    settings = RunHistorySettings(
        ops_bearer_token=SecretStr("ops-secret"),
        long_poll_max_clients=1,
    )
    client = _client(_service(registry, None, settings=settings))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            client.get,
            f"/api/runs/{run_id}/events?after_seq=1&wait_ms=1000",
            headers=_OPS_HEADERS,
        )
        deadline = time.monotonic() + 0.5
        while registry.wait_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        second = client.get(
            f"/api/runs/{run_id}/events?after_seq=1&wait_ms=1000",
            headers=_OPS_HEADERS,
        )
        registry.publish(run_id, _slice(run_id, (), latest_seq=1, terminal=True))
        assert first.result(timeout=1).status_code == 200

    assert second.status_code == 429
    assert second.headers["Retry-After"] == "1"


def test_event_timeout_and_unknown_retention_contract() -> None:
    run_id = "run-timeout"
    registry = _FakeRegistry()
    registry.event_slices[run_id] = _slice(run_id, (), latest_seq=2)
    timed_out = _client(_service(registry, None)).get(
        f"/api/runs/{run_id}/events?after_seq=2&wait_ms=10", headers=_OPS_HEADERS
    )
    missing = _client(_service(_FakeRegistry(), _FakeStore())).get(
        "/api/runs/expired/events", headers=_OPS_HEADERS
    )

    assert timed_out.status_code == 200
    assert timed_out.json()["events"] == []
    assert timed_out.json()["timed_out"] is True
    assert missing.status_code == 404
    assert missing.json()["detail"] == RunNotFoundError().detail


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "after_seq=-1", "wait_ms=25001"])
def test_event_endpoint_validates_public_bounds(query: str) -> None:
    response = _client(_service(_FakeRegistry(), None)).get(
        f"/api/runs/run-1/events?{query}", headers=_OPS_HEADERS
    )
    assert response.status_code == 422



def test_event_first_registry_lookup_is_tenant_scoped() -> None:
    run_id = "run-scope-first"
    registry = _FakeRegistry()
    registry.event_slices[run_id] = _slice(run_id, (), latest_seq=1, terminal=True)

    result = asyncio.run(_service(registry, None).get_events("opaque-a", run_id, 1, 200, 0))

    assert result.terminal is True
    assert registry.event_call_order[0] == "scoped:opaque-a"


def test_list_source_reports_memory_plus_sqlite_when_both_backends_are_queried() -> None:
    response = asyncio.run(
        _service(_FakeRegistry([_run_summary("memory-only-hit", 0)]), _FakeStore()).list_runs(
            "opaque-a", RunListFilters()
        )
    )
    assert response.source == "memory+sqlite"


def test_sqlite_detail_reducer_matches_retry_degraded_and_cancelled_rollups() -> None:
    run_id = "run-rollups"
    summary = replace(
        _run_summary(run_id, 0),
        last_seq=6,
        event_count=6,
        persistence_status="durable",
    )
    store = _FakeStore([summary])
    store.runs[run_id] = StoredRun(summary=summary, topology=MappingProxyType(_topology()))
    degraded = _event_for(run_id, 3, "degraded", 12.0)
    degraded["attributes"] = {"reason": "fallback"}
    retry_started = _event_for(run_id, 4, "retry.started", 13.0)
    retry_started["attempt"] = 2
    retry_started["attributes"] = {"reason": "transient"}
    retry_completed = _event_for(run_id, 5, "retry.completed", 18.0)
    retry_completed["attempt"] = 2
    retry_completed["duration_ms"] = 5.0
    cancelled = _event_for(run_id, 6, "node.cancelled", 20.0)
    cancelled["node_id"] = "generate"
    store.events[run_id] = (
        _event_for(run_id, 1, "run.started", 0.0),
        _event_for(run_id, 2, "node.started", 10.0),
        degraded,
        retry_started,
        retry_completed,
        cancelled,
    )

    detail = asyncio.run(_service(_FakeRegistry(), store).get_detail("scope-a", run_id))

    assert [(item.status, item.degraded_reason, item.retry_reason) for item in detail.node_rollup] == [
        ("running", "fallback", None),
        ("retry_completed", None, "transient"),
        ("cancelled", None, None),
    ]



def test_filter_hash_preserves_utc_microseconds() -> None:
    first = RunListFilters(started_after=NOW + timedelta(microseconds=1))
    second = RunListFilters(started_after=NOW + timedelta(microseconds=2))
    assert first.filter_hash() != second.filter_hash()


def test_list_discards_store_rows_newer_than_frozen_as_of() -> None:
    class FutureReturningStore(_FakeStore):
        def list_runs(
            self,
            tenant_scope: str,
            query: RunListQuery,
            *,
            before: tuple[datetime, str] | None = None,
        ) -> list[RunSummary]:
            self.list_scopes.append(tenant_scope)
            return list(self.summaries)

    future = _run_summary("future", 60)
    response = asyncio.run(
        _service(_FakeRegistry(), FutureReturningStore([future])).list_runs(
            "scope-a", RunListFilters()
        )
    )
    assert response.items == ()


def test_memory_partial_is_not_marked_durable_when_sqlite_watermark_lags() -> None:
    run_id = "run-durable-lag"
    registry = _FakeRegistry()
    registry.event_slices[run_id] = _slice(
        run_id,
        (_event_for(run_id, 5, "node.started", 50.0),),
        latest_seq=5,
        earliest=5,
        integrity="partial",
        persistence="pending",
    )
    durable_summary = replace(
        _run_summary(run_id, 0, status="running"),
        last_seq=3,
        event_count=3,
        persistence_status="durable",
    )
    store = _FakeStore([durable_summary])
    store.runs[run_id] = StoredRun(
        summary=durable_summary, topology=MappingProxyType(_topology())
    )
    store.events[run_id] = ()

    result = asyncio.run(_service(registry, store).get_events("scope-a", run_id, 4, 200, 0))

    assert result.history_state == "partial"
    assert result.persistence_status == "pending"


def test_naive_list_time_bound_is_rejected_as_client_error() -> None:
    response = _client(_service(_FakeRegistry(), None)).get(
        "/api/runs?started_after=2026-08-23T08:00:00", headers=_OPS_HEADERS
    )
    assert response.status_code == 422


def test_health_keeps_configured_persistence_enabled_when_store_is_unavailable() -> None:
    payload = asyncio.run(_service(_FakeRegistry(), None).health())
    assert payload.status == "degraded"
    assert payload.persistence.enabled is True
    assert payload.persistence.state == "memory_only"



class _HealthRegistry(_FakeRegistry):
    def __init__(self, status: str, enabled: bool) -> None:
        super().__init__()
        self._status = status
        self._enabled = enabled

    def health_snapshot(self) -> RegistryHealth:
        value = super().health_snapshot()
        return replace(value, status=self._status, enabled=self._enabled)


class _HealthStore(_FakeStore):
    def __init__(self, status: str) -> None:
        super().__init__()
        self._status = status

    def health_snapshot(self) -> StoreHealth:
        return replace(super().health_snapshot(), status=self._status)


@pytest.mark.parametrize(
    ("registry_status", "registry_enabled", "store_status", "expected"),
    [
        ("degraded", True, "ok", "degraded"),
        ("ok", True, "degraded", "degraded"),
        ("disabled", False, "disabled", "disabled"),
    ],
)
def test_health_returns_200_for_degraded_and_disabled_capability_states(
    registry_status: str,
    registry_enabled: bool,
    store_status: str,
    expected: str,
) -> None:
    response = _client(
        _service(
            _HealthRegistry(registry_status, registry_enabled),
            _HealthStore(store_status),
        )
    ).get("/api/runs/health", headers=_OPS_HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == expected


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=101", "slow_ms=999", "slow_ms=3600001"],
)
def test_list_endpoint_validates_public_bounds(query: str) -> None:
    response = _client(_service(_FakeRegistry(), None)).get(
        f"/api/runs?{query}", headers=_OPS_HEADERS
    )
    assert response.status_code == 422


def test_list_endpoint_rejects_unavailable_fingerprint_and_invalid_cursor_safely() -> None:
    client = _client(_service(_FakeRegistry(), None))
    fingerprint = client.get("/api/runs?fingerprint=fp-1", headers=_OPS_HEADERS)
    cursor = client.get("/api/runs?cursor=tampered", headers=_OPS_HEADERS)

    assert fingerprint.status_code == 400
    assert fingerprint.json()["detail"]["code"] == "fingerprint_unavailable"
    assert cursor.status_code == 400
    assert cursor.json()["detail"]["code"] == "cursor_invalid"


def test_endpoint_forwards_repeated_status_in_sorted_order_including_cancelled() -> None:
    registry = _FakeRegistry()
    response = _client(_service(registry, None)).get(
        "/api/runs?status=failed&status=cancelled", headers=_OPS_HEADERS
    )
    assert response.status_code == 200
    assert registry.queries[-1].statuses == ("cancelled", "failed")



def _collect_run_pages(service: RunOpsService, *, limit: int = 100) -> list[str]:
    cursor: str | None = None
    run_ids: list[str] = []
    while True:
        page = asyncio.run(
            service.list_runs("scope-a", RunListFilters(limit=limit, cursor=cursor))
        )
        run_ids.extend(item.run_id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return run_ids


def test_memory_pagination_traverses_150_same_timestamp_runs() -> None:
    summaries = [_run_summary(f"run-{index:03}", 0) for index in range(150)]
    ids = _collect_run_pages(_service(_FakeRegistry(summaries), None))
    assert ids == sorted((item.run_id for item in summaries), reverse=True)


def test_sqlite_pagination_traverses_150_rows() -> None:
    summaries = [_run_summary(f"run-{index:03}", index) for index in range(150)]
    ids = _collect_run_pages(
        _service(_FakeRegistry(), _FakeStore(summaries), clock=_Clock(NOW + timedelta(seconds=200)))
    )
    assert ids == [item.run_id for item in sorted(summaries, key=lambda x: (x.started_at, x.run_id), reverse=True)]





def test_internal_event_gap_returns_409_instead_of_noncontiguous_200() -> None:
    run_id = "run-internal-gap"
    registry = _FakeRegistry()
    registry.event_slices[run_id] = _slice(
        run_id,
        tuple(
            _event_for(run_id, seq, "node.started", float(seq))
            for seq in (1, 2, 3, 5, 6)
        ),
        latest_seq=6,
        earliest=1,
        integrity="partial",
    )

    response = _client(_service(registry, None)).get(
        f"/api/runs/{run_id}/events?after_seq=0", headers=_OPS_HEADERS
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "run_event_gap",
        "earliest_available_seq": 5,
        "latest_seq": 6,
    }



class _FailingReadStore(_FakeStore):
    def list_runs(self, *args: Any, **kwargs: Any) -> list[RunSummary]:
        raise RunHistoryReadError()

    def get_run(self, *args: Any, **kwargs: Any) -> StoredRun | None:
        raise RunHistoryReadError()

    def get_events(self, *args: Any, **kwargs: Any) -> StoredEventSlice | None:
        raise RunHistoryReadError()

    def durable_watermark(self, *args: Any, **kwargs: Any) -> int:
        raise RunHistoryReadError()


def test_list_read_failure_without_memory_fallback_returns_safe_503() -> None:
    response = _client(_service(_FakeRegistry(), _FailingReadStore())).get(
        "/api/runs", headers=_OPS_HEADERS
    )
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "run_ops_unavailable",
        "message": "运行历史暂不可用",
    }


def test_list_read_failure_with_memory_fallback_is_partial_memory_source() -> None:
    response = _client(
        _service(_FakeRegistry([_run_summary("run-memory", 0)]), _FailingReadStore())
    ).get("/api/runs", headers=_OPS_HEADERS)
    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()["items"]] == ["run-memory"]
    assert response.json()["source"] == "memory"
    assert response.json()["history_state"] == "partial"


@pytest.mark.parametrize("path", ["/api/runs/run-a", "/api/runs/run-a/events"])
def test_detail_and_events_read_failure_without_memory_return_503(path: str) -> None:
    response = _client(_service(_FakeRegistry(), _FailingReadStore())).get(
        path, headers=_OPS_HEADERS
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "run_ops_unavailable"



def test_non_time_value_error_maps_to_safe_503_not_invalid_time_422() -> None:
    class ValueErrorRegistry(_FakeRegistry):
        def list_runs(
            self, query: RunListQuery, *, before: tuple[datetime, str] | None = None
        ) -> list[RunSummary]:
            raise ValueError("projection failed")

    response = _client(_service(ValueErrorRegistry(), None)).get(
        "/api/runs", headers=_OPS_HEADERS
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "run_ops_unavailable"


def test_cancelled_long_poll_releases_single_admission_slot() -> None:
    run_id = "run-cancel-slot"
    registry = _FakeRegistry()
    registry.event_slices[run_id] = _slice(run_id, (), latest_seq=1)
    service = _service(
        registry,
        None,
        settings=RunHistorySettings(
            ops_bearer_token=SecretStr("ops-secret"), long_poll_max_clients=1
        ),
    )

    async def scenario() -> None:
        first = asyncio.create_task(service.get_events("scope-a", run_id, 1, 200, 25000))
        while registry.wait_calls < 1:
            await asyncio.sleep(0.005)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(service.get_events("scope-a", run_id, 1, 200, 25000))
        deadline = asyncio.get_running_loop().time() + 0.5
        while registry.wait_calls < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.005)
        assert registry.wait_calls >= 2
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        registry.publish(run_id, _slice(run_id, (), latest_seq=1, terminal=True))

    asyncio.run(scenario())


def test_warm_sqlite_events_1000_and_list_100000_meet_release_budgets(tmp_path) -> None:
    settings = RunHistorySettings(
        sqlite_path=str(tmp_path / "run-history.sqlite3"),
        writer_queue_capacity=256,
        writer_batch_size=64,
        writer_flush_ms=100,
        ops_bearer_token=SecretStr("ops-secret"),
    )
    store = RunHistoryStore.open(settings, "boot-a", "worker-a", now=lambda: NOW)
    scope = derive_tenant_scope("tenant-a", b"s" * 32)
    topology_json = json.dumps(_topology(), ensure_ascii=False, separators=(",", ":"))
    base_us = int(NOW.timestamp() * 1_000_000) - 100_000
    run_sql = """INSERT INTO runs (
        run_id,tenant_scope,boot_id,worker_id,status,outcome,started_at_us,updated_at_us,
        finished_at_us,elapsed_ms,topology_id,topology_revision,executor,topology_json,
        last_seq,event_count,earliest_available_seq,current_node_ids_json,failed_node_ids_json,
        route,degraded_count,retry_count,event_integrity,interruption_reason,query_fingerprint
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    try:
        with sqlite3.connect(store.database_path) as connection:
            connection.executemany(
                run_sql,
                (
                    (
                        f"run-budget-{index:06d}", scope, "boot-a", "worker-a", "completed",
                        "answered", base_us + index, base_us + index, base_us + index, 1.0,
                        "rag.query", "sha256:abc", "sequential_stream", topology_json,
                        1, 1, 1, "[]", "[]", None, 0, 0, "complete", None, None,
                    )
                    for index in range(100_000)
                ),
            )
            run_id = "run-budget-099999"
            connection.execute(
                "UPDATE runs SET last_seq=1000,event_count=1000 WHERE run_id=?", (run_id,)
            )
            event_sql = """INSERT INTO run_events
                (run_id,seq,event_type,node_id,attempt,occurred_at_us,elapsed_ms,duration_ms,event_json)
                VALUES (?,?,?,?,?,?,?,?,?)"""
            def event_row(seq: int) -> tuple[Any, ...]:
                event_type = "run.started" if seq == 1 else (
                    "run.completed" if seq == 1000 else "degraded"
                )
                event = _event_for(run_id, seq, event_type, float(seq))
                event["attributes"] = (
                    {"executor": "sequential_stream"} if seq == 1 else
                    {"outcome": "answered"} if seq == 1000 else
                    {"reason": "budget"}
                )
                event_json = json.dumps(event, default=str, ensure_ascii=False, separators=(",", ":"))
                return (
                    run_id, seq, event_type, None, None, base_us + seq, float(seq), None, event_json
                )
            connection.executemany(event_sql, (event_row(seq) for seq in range(1, 1001)))

        client = _client(_service(_FakeRegistry(), store, settings=settings))
        list_path = "/api/runs?limit=50"
        events_first = f"/api/runs/{run_id}/events?after_seq=0&limit=500"
        events_second = f"/api/runs/{run_id}/events?after_seq=500&limit=500"
        assert client.get(list_path, headers=_OPS_HEADERS).status_code == 200
        assert client.get(events_first, headers=_OPS_HEADERS).status_code == 200
        assert client.get(events_second, headers=_OPS_HEADERS).status_code == 200

        list_samples: list[float] = []
        event_samples: list[float] = []
        for _ in range(20):
            started = time.perf_counter()
            response = client.get(list_path, headers=_OPS_HEADERS)
            list_samples.append((time.perf_counter() - started) * 1000)
            assert response.status_code == 200 and len(response.json()["items"]) == 50

            started = time.perf_counter()
            first = client.get(events_first, headers=_OPS_HEADERS)
            second = client.get(events_second, headers=_OPS_HEADERS)
            event_samples.append((time.perf_counter() - started) * 1000)
            assert first.status_code == second.status_code == 200
            assert len(first.json()["events"]) == 500
            assert len(second.json()["events"]) == 500

        list_samples.sort()
        event_samples.sort()
        assert list_samples[int(len(list_samples) * 0.95) - 1] < 150.0
        assert event_samples[int(len(event_samples) * 0.95) - 1] < 100.0
    finally:
        store.close(2000)