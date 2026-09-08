"""Strict authenticated HTTP boundary for Stage 26 Knowledge Serving."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.enterprise_knowledge_serving import (
    canonical_knowledge_serving_evidence_link,
    canonical_knowledge_serving_event,
    canonical_knowledge_serving_snapshot,
    canonical_knowledge_serving_stage_fact,
    canonical_safe_mapping,
)
from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset

EngineProvider = Callable[[], Any]
ReadinessProvider = Callable[[], Any]
ServiceProvider = Any
_STAGE26_CAPABILITY = "enterprise_knowledge_serving_reliability"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
_DATASET_ID = Annotated[
    str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
]
_RESOURCE_ID = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)]
_IDEMPOTENCY_KEY = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProfileCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    workspace_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
    )
    reason: str = Field(min_length=1, max_length=512)


class PolicyRevisionCreateRequest(StrictModel):
    expected_profile_revision: int = Field(ge=1, le=_MAX_SAFE_INTEGER, strict=True)
    expected_policy_digest: str | None = Field(
        ..., min_length=64, max_length=64, pattern=_DIGEST.pattern
    )
    max_source_staleness_seconds: int = Field(ge=0, le=_MAX_SAFE_INTEGER, strict=True)
    max_parse_lag_seconds: int = Field(ge=0, le=_MAX_SAFE_INTEGER, strict=True)
    max_index_lag_seconds: int = Field(ge=0, le=_MAX_SAFE_INTEGER, strict=True)
    max_failed_document_count: int = Field(ge=0, le=_MAX_SAFE_INTEGER, strict=True)
    max_pending_index_count: int = Field(ge=0, le=_MAX_SAFE_INTEGER, strict=True)
    require_current_release: bool = Field(strict=True)
    require_passing_certification: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=512)


class ActivatePolicyRequest(StrictModel):
    policy_revision_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)
    expected_profile_revision: int = Field(ge=1, le=_MAX_SAFE_INTEGER, strict=True)
    expected_policy_digest: str = Field(min_length=64, max_length=64, pattern=_DIGEST.pattern)
    reason: str = Field(min_length=1, max_length=512)


class PreviewRequest(StrictModel):
    profile_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)
    expected_profile_revision: int = Field(ge=1, le=_MAX_SAFE_INTEGER, strict=True)
    expected_policy_digest: str | None = Field(
        ..., min_length=64, max_length=64, pattern=_DIGEST.pattern
    )
    max_source_staleness_seconds: int = Field(ge=0, le=_MAX_SAFE_INTEGER, strict=True)
    max_parse_lag_seconds: int = Field(ge=0, le=_MAX_SAFE_INTEGER, strict=True)
    max_index_lag_seconds: int = Field(ge=0, le=_MAX_SAFE_INTEGER, strict=True)
    max_failed_document_count: int = Field(ge=0, le=_MAX_SAFE_INTEGER, strict=True)
    max_pending_index_count: int = Field(ge=0, le=_MAX_SAFE_INTEGER, strict=True)
    require_current_release: bool = Field(strict=True)
    require_passing_certification: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=512)


class DigestQuery(StrictModel):
    value: str


def _safe_text(value: str, field: str) -> str:
    try:
        canonical_safe_mapping({field: value})
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{field} contains forbidden or unsafe content") from exc
    return value


ProfileStatus = Literal["draft", "active", "paused", "archived"]
SnapshotState = Literal["ready", "degraded", "blocked", "unavailable"]
StageState = Literal["ready", "lagging", "blocked", "missing", "unavailable"]
StageCode = Literal["source", "parse", "chunk", "index", "serve"]

_PROFILE_KEYS = frozenset(
    {
        "id",
        "tenant_id",
        "workspace_id",
        "dataset_id",
        "name",
        "normalized_name",
        "status",
        "active_profile_key",
        "revision",
        "current_policy_revision_id",
        "current_snapshot_id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "archived_at",
        "archived_by",
    }
)
_POLICY_KEYS = frozenset(
    {
        "id",
        "tenant_id",
        "profile_id",
        "revision",
        "max_source_staleness_seconds",
        "max_parse_lag_seconds",
        "max_index_lag_seconds",
        "max_failed_document_count",
        "max_pending_index_count",
        "require_current_release",
        "require_passing_certification",
        "policy_digest",
        "created_at",
        "created_by",
    }
)
_SNAPSHOT_KEYS = frozenset(
    {
        "id",
        "tenant_id",
        "profile_id",
        "policy_revision_id",
        "observation_key",
        "state",
        "source_count",
        "ready_source_count",
        "stale_source_count",
        "active_document_count",
        "failed_document_count",
        "pending_index_count",
        "expected_serving_generation",
        "observed_serving_generation",
        "current_release_id",
        "current_certification_id",
        "stage_count",
        "ready_stage_count",
        "blocked_stage_count",
        "snapshot_digest",
        "as_of",
        "created_at",
        "created_by",
    }
)
_STAGE_KEYS = frozenset(
    {
        "id",
        "tenant_id",
        "profile_id",
        "snapshot_id",
        "stage_code",
        "sequence",
        "state",
        "item_count",
        "ready_count",
        "warning_count",
        "pending_count",
        "error_count",
        "lag_seconds",
        "expected_revision",
        "observed_revision",
        "expected_digest",
        "observed_digest",
        "safe_error_code",
        "safe_error",
        "stage_digest",
        "observed_at",
    }
)
_EVIDENCE_KEYS = frozenset(
    {
        "id",
        "tenant_id",
        "profile_id",
        "snapshot_id",
        "stage_fact_id",
        "evidence_kind",
        "resource_id",
        "resource_revision",
        "resource_digest",
        "route_code",
        "safe_label",
        "evidence_digest",
        "created_at",
    }
)
_EVENT_KEYS = frozenset(
    {
        "id",
        "tenant_id",
        "profile_id",
        "snapshot_id",
        "stream_key",
        "sequence",
        "event_type",
        "previous_event_digest",
        "event_digest",
        "actor_id",
        "request_id",
        "safe_snapshot",
        "occurred_at",
    }
)
_SUMMARY_KEYS = frozenset(
    {
        "tenant_id",
        "dataset_id",
        "profile_id",
        "profile_name",
        "profile_status",
        "state",
        "as_of",
        "snapshot_id",
        "snapshot_digest",
        "policy_revision",
        "serving_generation",
        "source_count",
        "ready_source_count",
        "stale_source_count",
        "active_document_count",
        "failed_document_count",
        "pending_index_count",
        "stage_count",
        "ready_stage_count",
        "blocked_stage_count",
        "current_release_id",
        "current_certification_id",
        "stage_facts",
        "reason_code",
    }
)
_PAGE_KEYS = frozenset({"items", "count", "next_cursor", "invalid_item_count"})
_MUTATION_KEYS = frozenset(
    {"state", "operation", "resource_id", "revision", "message", "retryable"}
)
_MUTATION_STATES = frozenset(
    {"applied", "replayed", "conflict", "blocked", "rejected", "unavailable"}
)
_DETAIL_KEYS = frozenset({"snapshot", "stage_facts", "evidence_links", "events"})
_PREVIEW_KEYS = frozenset({"preview", "state", "policy_revision", "stage_facts", "blockers"})


def unavailable(message: str = "Knowledge Serving 服务暂不可用") -> HTTPException:
    return HTTPException(
        status_code=503, detail={"code": "knowledge_serving_unavailable", "message": message}
    )


def _readiness_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _stage26_is_ready(value: Any) -> bool:
    status = _readiness_value(value, "status")
    mutations_safe = _readiness_value(value, "mutations_safe")
    missing_groups = _readiness_value(value, "missing_capability_groups")
    return (
        status == "ready"
        and mutations_safe is True
        and isinstance(missing_groups, (list, tuple, set, frozenset))
        and _STAGE26_CAPABILITY not in missing_groups
    )


def _require_stage26_readiness(provider: ReadinessProvider | None) -> None:
    if provider is None:
        raise unavailable("Stage26 Knowledge Serving readiness provider is not configured")
    try:
        report = provider()
    except Exception as exc:  # noqa: BLE001
        raise unavailable("Stage26 Knowledge Serving readiness could not be verified") from exc
    if not _stage26_is_ready(report):
        raise unavailable("Stage26 Knowledge Serving capability is not ready")


def _safe_error(exc: Exception) -> HTTPException:
    status = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if (
        type(status) is int
        and 400 <= status <= 599
        and isinstance(code, str)
        and _ERROR_CODE.fullmatch(code)
        and isinstance(message, str)
    ):
        return HTTPException(status_code=status, detail={"code": code, "message": message})
    return unavailable()


def _result(value: Any) -> tuple[dict[str, Any], int]:
    if isinstance(value, Mapping):
        return dict(value), 200
    body = getattr(value, "body", None)
    status = getattr(value, "status", 200)
    if not isinstance(body, Mapping) or type(status) is not int:
        raise unavailable("Knowledge Serving 返回无效")
    return dict(body), status


def _wire_object(value: Any, keys: frozenset[str], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise unavailable(f"Knowledge Serving {field} 返回无效")
    return dict(value)


def _wire_identifier(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise unavailable(f"Knowledge Serving {field} identity is invalid")
    return value


def _wire_integer(
    value: Any,
    field: str,
    *,
    minimum: int = 0,
    maximum: int = _MAX_SAFE_INTEGER,
) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise unavailable(f"Knowledge Serving {field} integer is invalid")
    return value


def _wire_optional_integer(value: Any, field: str, *, minimum: int = 0) -> int | None:
    if value is None:
        return None
    return _wire_integer(value, field, minimum=minimum)


def _wire_digest(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise unavailable(f"Knowledge Serving {field} digest is invalid")
    return value


def _require_scope_identity(
    value: Mapping[str, Any],
    field: str,
    *,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
    profile_id: str | None = None,
    snapshot_id: str | None = None,
) -> None:
    actual_tenant = _wire_identifier(value.get("tenant_id"), f"{field}.tenant_id")
    if tenant_id is not None and actual_tenant != tenant_id:
        raise unavailable(f"Knowledge Serving {field} tenant scope is invalid")
    if dataset_id is not None:
        actual_dataset = value.get("dataset_id")
        if not isinstance(actual_dataset, str) or actual_dataset != dataset_id:
            raise unavailable(f"Knowledge Serving {field} dataset scope is invalid")
    if profile_id is not None:
        actual_profile = _wire_identifier(value.get("profile_id"), f"{field}.profile_id")
        if actual_profile != profile_id:
            raise unavailable(f"Knowledge Serving {field} profile ownership is invalid")
    if snapshot_id is not None:
        actual_snapshot = _wire_identifier(value.get("snapshot_id"), f"{field}.snapshot_id")
        if actual_snapshot != snapshot_id:
            raise unavailable(f"Knowledge Serving {field} snapshot ownership is invalid")


def _wire_policy(
    value: Any,
    field: str,
    *,
    tenant_id: str | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    policy = _wire_object(value, _POLICY_KEYS, field)
    _require_scope_identity(policy, field, tenant_id=tenant_id, profile_id=profile_id)
    _wire_identifier(policy["id"], f"{field}.id")
    _wire_integer(policy["revision"], f"{field}.revision", minimum=1)
    for name in (
        "max_source_staleness_seconds",
        "max_parse_lag_seconds",
        "max_index_lag_seconds",
        "max_failed_document_count",
        "max_pending_index_count",
    ):
        _wire_integer(policy[name], f"{field}.{name}")
    if type(policy["require_current_release"]) is not bool or type(
        policy["require_passing_certification"]
    ) is not bool:
        raise unavailable(f"Knowledge Serving {field} boolean is invalid")
    _wire_digest(policy["policy_digest"], f"{field}.policy_digest")
    return policy


def _wire_stage_fact(
    value: Any,
    field: str,
    *,
    tenant_id: str | None = None,
    profile_id: str | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    stage = _wire_object(value, _STAGE_KEYS, field)
    _require_scope_identity(
        stage,
        field,
        tenant_id=tenant_id,
        profile_id=profile_id,
        snapshot_id=snapshot_id,
    )
    _wire_identifier(stage["id"], f"{field}.id")
    expected_sequences = {"source": 1, "parse": 2, "chunk": 3, "index": 4, "serve": 5}
    stage_code = stage["stage_code"]
    if not isinstance(stage_code, str) or stage_code not in expected_sequences:
        raise unavailable(f"Knowledge Serving {field}.stage_code is invalid")
    sequence = _wire_integer(stage["sequence"], f"{field}.sequence", minimum=1, maximum=5)
    if sequence != expected_sequences[stage_code]:
        raise unavailable(f"Knowledge Serving {field}.sequence is invalid")
    if not isinstance(stage["state"], str) or stage["state"] not in {
        "ready",
        "lagging",
        "blocked",
        "missing",
        "unavailable",
    }:
        raise unavailable(f"Knowledge Serving {field}.state is invalid")
    for name in ("item_count", "ready_count", "warning_count", "pending_count", "error_count"):
        _wire_integer(stage[name], f"{field}.{name}")
    if any(
        stage[name] > stage["item_count"]
        for name in ("ready_count", "warning_count", "pending_count", "error_count")
    ):
        raise unavailable(f"Knowledge Serving {field} counters are inconsistent")
    _wire_integer(stage["lag_seconds"], f"{field}.lag_seconds")
    _wire_optional_integer(stage["expected_revision"], f"{field}.expected_revision")
    _wire_optional_integer(stage["observed_revision"], f"{field}.observed_revision")
    _wire_digest(stage["expected_digest"], f"{field}.expected_digest", optional=True)
    _wire_digest(stage["observed_digest"], f"{field}.observed_digest", optional=True)
    _wire_digest(stage["stage_digest"], f"{field}.stage_digest")
    if (stage["safe_error_code"] is None) != (stage["safe_error"] is None):
        raise unavailable(f"Knowledge Serving {field} safe error is inconsistent")
    return stage


def _wire_snapshot(
    value: Any,
    field: str,
    *,
    tenant_id: str | None = None,
    profile_id: str | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    snapshot = _wire_object(value, _SNAPSHOT_KEYS, field)
    _require_scope_identity(
        snapshot,
        field,
        tenant_id=tenant_id,
        profile_id=profile_id,
    )
    _wire_identifier(snapshot["id"], f"{field}.id")
    if snapshot_id is not None and snapshot["id"] != snapshot_id:
        raise unavailable(f"Knowledge Serving {field} snapshot ownership is invalid")
    _wire_identifier(snapshot["profile_id"], f"{field}.profile_id")
    _wire_identifier(snapshot["policy_revision_id"], f"{field}.policy_revision_id")
    _wire_identifier(snapshot["observation_key"], f"{field}.observation_key")
    if snapshot["state"] not in {"ready", "degraded", "blocked", "unavailable"}:
        raise unavailable(f"Knowledge Serving {field}.state is invalid")
    for name in (
        "source_count",
        "ready_source_count",
        "stale_source_count",
        "active_document_count",
        "failed_document_count",
        "pending_index_count",
    ):
        _wire_integer(snapshot[name], f"{field}.{name}")
    if snapshot["ready_source_count"] > snapshot["source_count"] or snapshot[
        "stale_source_count"
    ] > snapshot["source_count"]:
        raise unavailable(f"Knowledge Serving {field} source counters are inconsistent")
    _wire_integer(snapshot["expected_serving_generation"], f"{field}.expected_serving_generation")
    _wire_integer(snapshot["observed_serving_generation"], f"{field}.observed_serving_generation")
    _wire_identifier(snapshot["current_release_id"], f"{field}.current_release_id", optional=True)
    _wire_identifier(
        snapshot["current_certification_id"],
        f"{field}.current_certification_id",
        optional=True,
    )
    _wire_integer(snapshot["stage_count"], f"{field}.stage_count", minimum=5, maximum=5)
    _wire_integer(snapshot["ready_stage_count"], f"{field}.ready_stage_count", maximum=5)
    _wire_integer(snapshot["blocked_stage_count"], f"{field}.blocked_stage_count", maximum=5)
    if (
        snapshot["ready_stage_count"] > snapshot["stage_count"]
        or snapshot["blocked_stage_count"] > snapshot["stage_count"]
        or snapshot["ready_stage_count"] + snapshot["blocked_stage_count"]
        > snapshot["stage_count"]
    ):
        raise unavailable(f"Knowledge Serving {field} stage counters are inconsistent")
    _wire_digest(snapshot["snapshot_digest"], f"{field}.snapshot_digest")
    return snapshot


def _wire_evidence(
    value: Any,
    field: str,
    *,
    tenant_id: str | None = None,
    profile_id: str | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    evidence = _wire_object(value, _EVIDENCE_KEYS, field)
    _require_scope_identity(
        evidence,
        field,
        tenant_id=tenant_id,
        profile_id=profile_id,
        snapshot_id=snapshot_id,
    )
    for name in ("id", "profile_id", "snapshot_id", "stage_fact_id"):
        _wire_identifier(evidence[name], f"{field}.{name}")
    _wire_identifier(evidence["resource_id"], f"{field}.resource_id")
    _wire_optional_integer(evidence["resource_revision"], f"{field}.resource_revision", minimum=1)
    _wire_digest(evidence["resource_digest"], f"{field}.resource_digest", optional=True)
    _wire_digest(evidence["evidence_digest"], f"{field}.evidence_digest")
    return evidence


def _wire_event(
    value: Any,
    field: str,
    *,
    tenant_id: str | None = None,
    profile_id: str | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    event = _wire_object(value, _EVENT_KEYS, field)
    _require_scope_identity(
        event,
        field,
        tenant_id=tenant_id,
        profile_id=profile_id,
        snapshot_id=snapshot_id,
    )
    _wire_identifier(event["id"], f"{field}.id")
    _wire_identifier(event["profile_id"], f"{field}.profile_id")
    _wire_identifier(event["snapshot_id"], f"{field}.snapshot_id", optional=True)
    _wire_identifier(event["stream_key"], f"{field}.stream_key")
    _wire_integer(event["sequence"], f"{field}.sequence", minimum=1)
    if not isinstance(event["event_type"], str) or _CODE.fullmatch(event["event_type"]) is None:
        raise unavailable(f"Knowledge Serving {field}.event_type is invalid")
    _wire_digest(event["previous_event_digest"], f"{field}.previous_event_digest", optional=True)
    _wire_digest(event["event_digest"], f"{field}.event_digest")
    _wire_identifier(event["actor_id"], f"{field}.actor_id")
    _wire_identifier(event["request_id"], f"{field}.request_id")
    return event


def _wire_page(
    value: Any,
    item_keys: frozenset[str],
    field: str,
    *,
    tenant_id: str | None = None,
    profile_id: str | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    page = _wire_object(value, _PAGE_KEYS, field)
    if not isinstance(page["items"], list):
        raise unavailable(f"Knowledge Serving {field} items is invalid")
    count = _wire_integer(page["count"], f"{field}.count")
    invalid_item_count = _wire_integer(page["invalid_item_count"], f"{field}.invalid_item_count")
    if invalid_item_count > count or len(page["items"]) > count:
        raise unavailable(f"Knowledge Serving {field} counts are inconsistent")
    page["count"] = count
    page["invalid_item_count"] = invalid_item_count
    if page["next_cursor"] is not None and not isinstance(page["next_cursor"], str):
        raise unavailable(f"Knowledge Serving {field} cursor is invalid")
    wired_items: list[dict[str, Any]] = []
    for index, item in enumerate(page["items"]):
        item_field = f"{field} item {index}"
        if item_keys == _SNAPSHOT_KEYS:
            wired_items.append(
                _wire_snapshot(
                    item,
                    item_field,
                    tenant_id=tenant_id,
                    profile_id=profile_id,
                )
            )
        elif item_keys == _STAGE_KEYS:
            wired_items.append(
                _wire_stage_fact(
                    item,
                    item_field,
                    tenant_id=tenant_id,
                    profile_id=profile_id,
                    snapshot_id=snapshot_id,
                )
            )
        elif item_keys == _EVENT_KEYS:
            wired_items.append(
                _wire_event(
                    item,
                    item_field,
                    tenant_id=tenant_id,
                    profile_id=profile_id,
                    snapshot_id=snapshot_id,
                )
            )
        else:
            wired_items.append(_wire_object(item, item_keys, item_field))
    page["items"] = wired_items
    return page

def _wire_profile(
    value: Any,
    field: str,
    *,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    profile = _wire_object(value, _PROFILE_KEYS, field)
    _require_scope_identity(profile, field, tenant_id=tenant_id, dataset_id=dataset_id)
    _wire_identifier(profile["id"], f"{field}.id")
    _wire_identifier(profile["workspace_id"], f"{field}.workspace_id", optional=True)
    _wire_identifier(profile["dataset_id"], f"{field}.dataset_id")
    _wire_identifier(profile["active_profile_key"], f"{field}.active_profile_key", optional=True)
    _wire_integer(profile["revision"], f"{field}.revision", minimum=1)
    _wire_identifier(
        profile["current_policy_revision_id"],
        f"{field}.current_policy_revision_id",
        optional=True,
    )
    _wire_identifier(profile["current_snapshot_id"], f"{field}.current_snapshot_id", optional=True)
    if profile["status"] not in {"draft", "active", "paused", "archived"}:
        raise unavailable(f"Knowledge Serving {field}.status is invalid")
    if profile["active_profile_key"] is not None and profile["active_profile_key"] != profile[
        "dataset_id"
    ]:
        raise unavailable(f"Knowledge Serving {field} active profile ownership is invalid")
    if profile["status"] == "active" and (
        profile["active_profile_key"] != profile["dataset_id"]
        or profile["current_policy_revision_id"] is None
    ):
        raise unavailable(f"Knowledge Serving {field} active state is invalid")
    if profile["status"] == "archived" and profile["active_profile_key"] is not None:
        raise unavailable(f"Knowledge Serving {field} archived state is invalid")
    return profile


def _wire_profile_read(
    value: Any,
    *,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    result = _wire_object(
        value, frozenset({"profile", "current_policy", "current_snapshot"}), "profile"
    )
    result["profile"] = _wire_profile(
        result["profile"],
        "profile.profile",
        tenant_id=tenant_id,
        dataset_id=dataset_id,
    )
    profile_value = result["profile"]
    profile_id = profile_value["id"]
    if result["current_policy"] is not None:
        result["current_policy"] = _wire_policy(
            result["current_policy"],
            "profile.policy",
            tenant_id=profile_value["tenant_id"],
            profile_id=profile_id,
        )
        if profile_value["current_policy_revision_id"] != result["current_policy"]["id"]:
            raise unavailable("Knowledge Serving profile policy ownership is invalid")
    if result["current_snapshot"] is not None:
        if profile_value["current_snapshot_id"] is None:
            raise unavailable("Knowledge Serving profile snapshot ownership is invalid")
        result["current_snapshot"] = _wire_snapshot(
            result["current_snapshot"],
            "profile.snapshot",
            tenant_id=profile_value["tenant_id"],
            profile_id=profile_id,
            snapshot_id=profile_value["current_snapshot_id"],
        )
    return result


def _wire_detail(
    value: Any,
    *,
    tenant_id: str | None = None,
    profile_id: str | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    result = _wire_object(value, _DETAIL_KEYS, "snapshot detail")
    result["snapshot"] = _wire_snapshot(
        result["snapshot"],
        "snapshot detail.snapshot",
        tenant_id=tenant_id,
        profile_id=profile_id,
        snapshot_id=snapshot_id,
    )
    snapshot_value = result["snapshot"]
    detail_tenant = snapshot_value["tenant_id"]
    detail_profile = snapshot_value["profile_id"]
    detail_snapshot = snapshot_value["id"]
    if not isinstance(result["stage_facts"], list):
        raise unavailable("Knowledge Serving detail stage facts are invalid")
    result["stage_facts"] = [
        _wire_stage_fact(
            item,
            f"snapshot detail.stage_facts item {index}",
            tenant_id=detail_tenant,
            profile_id=detail_profile,
            snapshot_id=detail_snapshot,
        )
        for index, item in enumerate(result["stage_facts"])
    ]
    if not isinstance(result["evidence_links"], list):
        raise unavailable("Knowledge Serving detail evidence links are invalid")
    result["evidence_links"] = [
        _wire_evidence(
            item,
            f"snapshot detail.evidence_links item {index}",
            tenant_id=detail_tenant,
            profile_id=detail_profile,
            snapshot_id=detail_snapshot,
        )
        for index, item in enumerate(result["evidence_links"])
    ]
    if not isinstance(result["events"], list):
        raise unavailable("Knowledge Serving detail events are invalid")
    result["events"] = [
        _wire_event(
            item,
            f"snapshot detail.events item {index}",
            tenant_id=detail_tenant,
            profile_id=detail_profile,
            snapshot_id=detail_snapshot,
        )
        for index, item in enumerate(result["events"])
    ]
    try:
        facts = [canonical_knowledge_serving_stage_fact(item) for item in result["stage_facts"]]
        evidence = [
            canonical_knowledge_serving_evidence_link(item) for item in result["evidence_links"]
        ]
        result["snapshot"] = canonical_knowledge_serving_snapshot(
            result["snapshot"], stage_facts=facts, evidence_links=evidence
        )
        for event in result["events"]:
            event_input = dict(event)
            event_input["safe_snapshot_json"] = event_input.pop("safe_snapshot")
            canonical_knowledge_serving_event(event_input)
    except Exception as exc:  # noqa: BLE001
        raise unavailable("Knowledge Serving snapshot detail canonical contract is invalid") from exc
    return result


def _wire_preview(
    value: Any,
    *,
    tenant_id: str | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    result = _wire_object(value, _PREVIEW_KEYS, "preview")
    if result["preview"] is not True:
        raise unavailable("Knowledge Serving preview marker is invalid")
    if result["state"] not in {"ready", "degraded", "blocked", "unavailable"}:
        raise unavailable("Knowledge Serving preview state is invalid")
    _wire_integer(result["policy_revision"], "preview.policy_revision", minimum=1)
    if not isinstance(result["stage_facts"], list) or not isinstance(result["blockers"], list):
        raise unavailable("Knowledge Serving preview list is invalid")
    result["stage_facts"] = [
        _wire_stage_fact(
            item,
            f"preview.stage_facts item {index}",
            tenant_id=tenant_id,
            profile_id=profile_id,
        )
        for index, item in enumerate(result["stage_facts"])
    ]
    for blocker in result["blockers"]:
        if not isinstance(blocker, Mapping) or set(blocker) != {
            "code",
            "stage_code",
            "safe_message",
        }:
            raise unavailable("Knowledge Serving preview blocker is invalid")
        if not isinstance(blocker["code"], str) or _ERROR_CODE.fullmatch(blocker["code"]) is None:
            raise unavailable("Knowledge Serving preview blocker code is invalid")
        if not isinstance(blocker["stage_code"], str) or blocker["stage_code"] not in {
            "source",
            "parse",
            "chunk",
            "index",
            "serve",
        }:
            raise unavailable("Knowledge Serving preview blocker stage is invalid")
        if not isinstance(blocker["safe_message"], str) or not blocker["safe_message"]:
            raise unavailable("Knowledge Serving preview blocker message is invalid")
    return result

def _wire_mutation(
    value: Any,
    operation: str,
    *,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise unavailable("Knowledge Serving mutation ������Ч")
    raw = dict(value)
    if not _MUTATION_KEYS.issubset(raw):
        raise unavailable("Knowledge Serving mutation ������Ч")
    if (
        raw["state"] not in _MUTATION_STATES
        or raw["operation"] != operation
        or not isinstance(raw["operation"], str)
        or not isinstance(raw["retryable"], bool)
    ):
        raise unavailable("Knowledge Serving mutation ������Ч")
    _wire_identifier(raw["resource_id"], "mutation.resource_id", optional=True)
    _wire_optional_integer(raw["revision"], "mutation.revision", minimum=1)
    if raw["message"] is not None and (
        not isinstance(raw["message"], str) or not raw["message"] or len(raw["message"]) > 512
    ):
        raise unavailable("Knowledge Serving mutation ������Ч")
    allowed = set(_MUTATION_KEYS)
    if operation == "create_serving_profile":
        allowed.add("profile")
        if "profile" not in raw:
            raise unavailable("Knowledge Serving profile mutation ������Ч")
        raw["profile"] = _wire_profile(
            raw["profile"],
            "mutation.profile",
            tenant_id=tenant_id,
            dataset_id=dataset_id,
        )
    elif operation == "create_serving_policy_revision":
        allowed.add("policy")
        if "policy" not in raw:
            raise unavailable("Knowledge Serving policy mutation ������Ч")
        raw["policy"] = _wire_policy(
            raw["policy"],
            "mutation.policy",
            tenant_id=tenant_id,
        )
        if "profile" in raw:
            allowed.add("profile")
            if raw["profile"] is not None:
                raw["profile"] = _wire_profile(
                    raw["profile"],
                    "mutation.profile",
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                )
                if raw["policy"]["profile_id"] != raw["profile"]["id"]:
                    raise unavailable("Knowledge Serving mutation profile ownership is invalid")
    elif operation == "activate_serving_policy":
        allowed.update({"profile", "policy"})
        raw["profile"] = _wire_profile(
            raw.get("profile"),
            "mutation.profile",
            tenant_id=tenant_id,
            dataset_id=dataset_id,
        )
        raw["policy"] = _wire_policy(
            raw.get("policy"),
            "mutation.policy",
            tenant_id=tenant_id,
            profile_id=raw["profile"]["id"],
        )
    if set(raw) != allowed:
        raise unavailable("Knowledge Serving mutation ����δ֪�ֶ�")
    return raw

def _wire_body(
    operation: str,
    value: Any,
    *,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
    profile_id: str | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    if operation == "get_serving_summary":
        result = _wire_object(value, _SUMMARY_KEYS, "summary")
        _require_scope_identity(
            result,
            "summary",
            tenant_id=tenant_id,
            dataset_id=dataset_id,
        )
        _wire_identifier(result["profile_id"], "summary.profile_id", optional=True)
        _wire_identifier(result["snapshot_id"], "summary.snapshot_id", optional=True)
        _wire_identifier(result["current_release_id"], "summary.current_release_id", optional=True)
        _wire_identifier(
            result["current_certification_id"],
            "summary.current_certification_id",
            optional=True,
        )
        _wire_digest(result["snapshot_digest"], "summary.snapshot_digest", optional=True)
        if result["state"] not in {"ready", "degraded", "blocked", "unavailable"}:
            raise unavailable("Knowledge Serving summary state is invalid")
        for name in (
            "source_count",
            "ready_source_count",
            "stale_source_count",
            "active_document_count",
            "failed_document_count",
            "pending_index_count",
            "stage_count",
            "ready_stage_count",
            "blocked_stage_count",
        ):
            maximum = 5 if name.endswith("stage_count") else _MAX_SAFE_INTEGER
            _wire_integer(result[name], f"summary.{name}", maximum=maximum)
        if (
            result["ready_source_count"] > result["source_count"]
            or result["stale_source_count"] > result["source_count"]
            or result["ready_stage_count"] > result["stage_count"]
            or result["blocked_stage_count"] > result["stage_count"]
            or result["ready_stage_count"] + result["blocked_stage_count"]
            > result["stage_count"]
        ):
            raise unavailable("Knowledge Serving summary counters are inconsistent")
        _wire_optional_integer(result["policy_revision"], "summary.policy_revision", minimum=1)
        _wire_optional_integer(result["serving_generation"], "summary.serving_generation")
        if not isinstance(result["stage_facts"], list):
            raise unavailable("Knowledge Serving summary stage facts are invalid")
        if result["stage_facts"] and (
            result["profile_id"] is None or result["snapshot_id"] is None
        ):
            raise unavailable("Knowledge Serving summary stage fact ownership is invalid")
        result["stage_facts"] = [
            _wire_stage_fact(
                item,
                f"summary.stage_facts item {index}",
                tenant_id=result["tenant_id"],
                profile_id=result["profile_id"],
                snapshot_id=result["snapshot_id"],
            )
            for index, item in enumerate(result["stage_facts"])
        ]
        return result
    if operation == "get_serving_profile":
        return _wire_profile_read(value, tenant_id=tenant_id, dataset_id=dataset_id)
    if operation == "list_serving_snapshots":
        return _wire_page(
            value,
            _SNAPSHOT_KEYS,
            "snapshots",
            tenant_id=tenant_id,
            profile_id=profile_id,
        )
    if operation == "get_serving_snapshot":
        return _wire_detail(
            value,
            tenant_id=tenant_id,
            profile_id=profile_id,
            snapshot_id=snapshot_id,
        )
    if operation == "list_serving_stage_facts":
        return _wire_page(
            value,
            _STAGE_KEYS,
            "stage facts",
            tenant_id=tenant_id,
            profile_id=profile_id,
            snapshot_id=snapshot_id,
        )
    if operation == "list_serving_events":
        return _wire_page(
            value,
            _EVENT_KEYS,
            "events",
            tenant_id=tenant_id,
            profile_id=profile_id,
            snapshot_id=snapshot_id,
        )
    if operation == "preview_serving_profile":
        return _wire_preview(value, tenant_id=tenant_id, profile_id=profile_id)
    return _wire_mutation(
        value,
        operation,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
    )

def response(
    value: Any,
    *,
    operation: str,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
    profile_id: str | None = None,
    snapshot_id: str | None = None,
) -> JSONResponse:
    body, status = _result(value)
    if not 100 <= status <= 599:
        raise unavailable("Knowledge Serving status ������Ч")
    try:
        encoded = jsonable_encoder(body)
    except Exception as exc:  # noqa: BLE001
        raise unavailable("Knowledge Serving ���ز������л�") from exc
    return JSONResponse(
        _wire_body(
            operation,
            encoded,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            profile_id=profile_id,
            snapshot_id=snapshot_id,
        ),
        status_code=status,
    )

def load_service(injected: ServiceProvider | None) -> Any:
    if injected is not None:
        return injected
    from core import enterprise_knowledge_serving_service

    return enterprise_knowledge_serving_service


def service_call(service: Any, operation: str, engine: Any, **kwargs: Any) -> Any:
    try:
        method = getattr(service, operation)
        if not callable(method):
            raise AttributeError(operation)
        return method(engine, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _safe_error(exc) from exc


def _clean_key(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 128
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "knowledge_serving_request_invalid",
                "message": "Idempotency-Key is invalid",
            },
        )
    return normalized


def build_enterprise_knowledge_serving_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    service: ServiceProvider | None = None,
    actor_dependency: Callable[..., Any] | None = None,
    readiness_provider: ReadinessProvider | None = None,
) -> APIRouter:
    """Build only the approved Stage26 read/control/preview routes."""

    if not callable(read_engine_provider) or not callable(mutation_engine_provider):
        raise TypeError("engine providers must be callable")
    if actor_dependency is not None and not callable(actor_dependency):
        raise TypeError("actor_dependency must be callable")
    if readiness_provider is not None and not callable(readiness_provider):
        raise TypeError("readiness_provider must be callable")
    target = load_service(service)
    read_actor = actor_dependency or require_knowledge_permission(
        KNOWLEDGE_READ, resolve_path_dataset("dataset_id")
    )
    manage_actor = actor_dependency or require_knowledge_permission(
        KNOWLEDGE_MANAGE, resolve_path_dataset("dataset_id")
    )
    router = APIRouter()

    def read(operation: str, actor: KnowledgeActor, **kwargs: Any) -> JSONResponse:
        _require_stage26_readiness(readiness_provider)
        value = service_call(
            target,
            operation,
            read_engine_provider(),
            tenant_id=actor.tenant_id,
            actor_id=actor.account_id,
            request_id=actor.request_id,
            **kwargs,
        )
        return response(
            value,
            operation=operation,
            tenant_id=actor.tenant_id,
            dataset_id=kwargs.get("dataset_id"),
            profile_id=kwargs.get("profile_id"),
            snapshot_id=kwargs.get("snapshot_id"),
        )

    def mutate(operation: str, actor: KnowledgeActor, key: str, **kwargs: Any) -> JSONResponse:
        _require_stage26_readiness(readiness_provider)
        value = service_call(
            target,
            operation,
            mutation_engine_provider(),
            tenant_id=actor.tenant_id,
            actor_id=actor.account_id,
            request_id=actor.request_id,
            idempotency_key=_clean_key(key),
            **kwargs,
        )
        return response(
            value,
            operation=operation,
            tenant_id=actor.tenant_id,
            dataset_id=kwargs.get("dataset_id"),
            profile_id=kwargs.get("profile_id"),
            snapshot_id=kwargs.get("snapshot_id"),
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/serving/summary")
    def summary(
        dataset_id: _DATASET_ID, actor: KnowledgeActor = Depends(read_actor)
    ) -> JSONResponse:
        return read("get_serving_summary", actor, dataset_id=dataset_id)

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/serving/profile")
    def profile(
        dataset_id: _DATASET_ID, actor: KnowledgeActor = Depends(read_actor)
    ) -> JSONResponse:
        return read("get_serving_profile", actor, dataset_id=dataset_id)

    @router.post("/api/enterprise/knowledge-bases/{dataset_id}/serving/profile", status_code=201)
    def create_profile(
        dataset_id: _DATASET_ID,
        body: ProfileCreateRequest,
        key: _IDEMPOTENCY_KEY,
        actor: KnowledgeActor = Depends(manage_actor),
    ) -> JSONResponse:
        return mutate(
            "create_serving_profile", actor, key, dataset_id=dataset_id, **body.model_dump()
        )

    @router.post(
        "/api/enterprise/knowledge-bases/{dataset_id}/serving/profile/revisions", status_code=201
    )
    def create_policy(
        dataset_id: _DATASET_ID,
        body: PolicyRevisionCreateRequest,
        key: _IDEMPOTENCY_KEY,
        actor: KnowledgeActor = Depends(manage_actor),
    ) -> JSONResponse:
        return mutate(
            "create_serving_policy_revision", actor, key, dataset_id=dataset_id, **body.model_dump()
        )

    @router.post("/api/enterprise/knowledge-bases/{dataset_id}/serving/profile/activate")
    def activate(
        dataset_id: _DATASET_ID,
        body: ActivatePolicyRequest,
        key: _IDEMPOTENCY_KEY,
        actor: KnowledgeActor = Depends(manage_actor),
    ) -> JSONResponse:
        return mutate(
            "activate_serving_policy", actor, key, dataset_id=dataset_id, **body.model_dump()
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/serving/snapshots")
    def snapshots(
        dataset_id: _DATASET_ID,
        actor: KnowledgeActor = Depends(read_actor),
        cursor: str | None = Query(default=None, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
        state: SnapshotState | None = Query(default=None),
        profile_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
        ),
    ) -> JSONResponse:
        return read(
            "list_serving_snapshots",
            actor,
            dataset_id=dataset_id,
            cursor=cursor,
            limit=limit,
            state=state,
            profile_id=profile_id,
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/serving/snapshots/{snapshot_id}")
    def snapshot_detail(
        dataset_id: _DATASET_ID,
        snapshot_id: _RESOURCE_ID,
        actor: KnowledgeActor = Depends(read_actor),
    ) -> JSONResponse:
        return read("get_serving_snapshot", actor, dataset_id=dataset_id, snapshot_id=snapshot_id)

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/serving/stage-facts")
    def stage_facts(
        dataset_id: _DATASET_ID,
        actor: KnowledgeActor = Depends(read_actor),
        snapshot_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
        ),
        profile_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
        ),
        stage_code: StageCode | None = Query(default=None),
    ) -> JSONResponse:
        return read(
            "list_serving_stage_facts",
            actor,
            dataset_id=dataset_id,
            snapshot_id=snapshot_id,
            profile_id=profile_id,
            stage_code=stage_code,
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/serving/events")
    def events(
        dataset_id: _DATASET_ID,
        actor: KnowledgeActor = Depends(read_actor),
        snapshot_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
        ),
        profile_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
        ),
        event_type: str | None = Query(default=None, pattern=_CODE.pattern),
        cursor: str | None = Query(default=None, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        return read(
            "list_serving_events",
            actor,
            dataset_id=dataset_id,
            snapshot_id=snapshot_id,
            profile_id=profile_id,
            event_type=event_type,
            cursor=cursor,
            limit=limit,
        )

    @router.post("/api/enterprise/knowledge-bases/{dataset_id}/serving/preview")
    def preview(
        dataset_id: _DATASET_ID, body: PreviewRequest, actor: KnowledgeActor = Depends(read_actor)
    ) -> JSONResponse:
        _require_stage26_readiness(readiness_provider)
        return response(
            service_call(
                target,
                "preview_serving_profile",
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                request_id=actor.request_id,
                dataset_id=dataset_id,
                **body.model_dump(),
            ),
            operation="preview_serving_profile",
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            profile_id=body.profile_id,
        )

    return router


build_enterprise_knowledge_serving_api_router = build_enterprise_knowledge_serving_router

__all__ = [
    "ActivatePolicyRequest",
    "PolicyRevisionCreateRequest",
    "StageCode",
    "PreviewRequest",
    "ProfileCreateRequest",
    "build_enterprise_knowledge_serving_api_router",
    "build_enterprise_knowledge_serving_router",
    "load_service",
    "response",
    "service_call",
    "unavailable",
]
