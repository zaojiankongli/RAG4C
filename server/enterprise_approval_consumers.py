"""Execution consumers for approval-gated enterprise mutations.

Consumers are deliberately thin adapters: the approval authority owns the
one-time ticket and state machine, while this module re-checks the action
contract and invokes the existing Dataset ACL mutation service directly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import importlib
import json
import re
from typing import Any

from core.enterprise_access_mutations import disable_dataset_acl
from core.enterprise_directory import change_tenant_member_role

ACTION_DATASET_ACL_DISABLE = "dataset_acl_disable"
ACTION_MEMBER_ROLE_CHANGE = "member_role_change"
ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE = "workspace_authorization_mode_change"
ACTION_DATASET_WORKSPACE_TRANSFER = "dataset_workspace_transfer"
ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH = "knowledge_base_release_publish"
ACTION_KNOWLEDGE_BASE_RELEASE_ROLLBACK = "knowledge_base_release_rollback"
ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER = "knowledge_base_release_quality_waiver"
RESOURCE_KNOWLEDGE_BASE = "knowledge_base"
RESOURCE_TENANT_MEMBER = "tenant_member"
RESOURCE_TENANT_WORKSPACE = "tenant_workspace"
_MEMBER_ROLES = frozenset({"owner", "admin", "editor", "member"})
_WORKSPACE_AUTHORIZATION_MODES = frozenset({"disabled", "shadow", "enforced"})
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class ApprovalConsumerError(ValueError):
    """The approval payload cannot be safely executed by a consumer."""


def _text(payload: Mapping[str, Any], key: str, *, maximum: int = 256) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ApprovalConsumerError(f"{key} is required")
    result = value.strip()
    if not result or len(result) > maximum:
        raise ApprovalConsumerError(f"{key} is invalid")
    return result


def _positive_revision(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ApprovalConsumerError("expected_acl_revision is invalid")
    return value


def _consumer_actor(payload: Mapping[str, Any]) -> tuple[str, str]:
    actor_id = payload.get("consumer_actor_id")
    actor_role = payload.get("consumer_actor_role")
    nested = payload.get("consumer")
    if isinstance(nested, Mapping):
        actor_id = actor_id or nested.get("actor_id") or nested.get("id")
        actor_role = actor_role or nested.get("role")
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ApprovalConsumerError("consumer actor is required")
    if not isinstance(actor_role, str) or actor_role.strip().casefold() not in {"owner", "admin"}:
        raise ApprovalConsumerError("consumer actor must be an owner or admin")
    return actor_id.strip(), actor_role.strip().casefold()


def _request_evidence(payload: Mapping[str, Any], approval_request_id: str) -> tuple[str, str]:
    evidence = payload.get("request_evidence")
    if not isinstance(evidence, Mapping):
        evidence = {}
    request_id = evidence.get("request_id") or payload.get("request_id_header")
    request_ip = evidence.get("request_ip") or payload.get("request_ip") or ""
    if not isinstance(request_id, str) or not request_id.strip():
        request_id = f"approval-execution:{approval_request_id}"
    if not isinstance(request_ip, str):
        request_ip = ""
    return request_id.strip()[:128], request_ip.strip()[:64]


def _service_result_body(value: Any, *, label: str) -> Mapping[str, Any]:
    """Unwrap a core ServiceResult without persisting its transport envelope."""

    if isinstance(value, Mapping):
        return value
    body = getattr(value, "body", None)
    response_status = getattr(value, "status", 200)
    if isinstance(body, Mapping) and type(response_status) is int and 200 <= response_status <= 299:
        return body
    raise ApprovalConsumerError(f"{label} result is invalid")


def _safe_acl_result(value: Any) -> dict[str, Any]:
    """Keep only the stable Dataset projection returned to the approval API."""

    value = _service_result_body(value, label="dataset ACL mutation")
    if not isinstance(value, Mapping):
        raise ApprovalConsumerError("dataset ACL mutation result is invalid")
    dataset = value.get("dataset")
    if not isinstance(dataset, Mapping):
        raise ApprovalConsumerError("dataset ACL mutation result is incomplete")
    dataset_id = dataset.get("id")
    tenant_id = dataset.get("tenant_id")
    mode = dataset.get("acl_mode")
    revision = dataset.get("acl_revision")
    if not all(isinstance(item, str) and item.strip() for item in (dataset_id, tenant_id, mode)):
        raise ApprovalConsumerError("dataset ACL mutation result is invalid")
    if type(revision) is not int or revision < 1:
        raise ApprovalConsumerError("dataset ACL mutation result revision is invalid")
    return {
        "dataset": {
            "id": dataset_id.strip(),
            "tenant_id": tenant_id.strip(),
            "acl_mode": mode.strip(),
            "acl_revision": revision,
        }
    }


def build_dataset_acl_disable_consumer(
    mutation_engine_provider: Callable[[], Any],
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build the action adapter with a lazy writable catalog provider."""

    if not callable(mutation_engine_provider):
        raise TypeError("mutation_engine_provider must be callable")

    def consume(payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ApprovalConsumerError("approval execution payload must be an object")
        action_type = _text(payload, "action_type", maximum=64)
        if action_type != ACTION_DATASET_ACL_DISABLE:
            raise ApprovalConsumerError("unsupported approval action")
        resource_type = _text(payload, "resource_type", maximum=64)
        if resource_type != RESOURCE_KNOWLEDGE_BASE:
            raise ApprovalConsumerError("unsupported approval resource type")
        resource_id = _text(payload, "resource_id", maximum=256)
        approval_request_id = _text(
            payload,
            "approval_request_id",
            maximum=64,
        )
        execution_id = _text(payload, "execution_id", maximum=128)
        actor_id, actor_role = _consumer_actor(payload)
        requester_id = _text(payload, "requester_id", maximum=64)
        reason = _text(payload, "reason", maximum=512)
        snapshot = payload.get("request_snapshot")
        if snapshot is None:
            snapshot = payload.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise ApprovalConsumerError("request snapshot is required")
        if snapshot.get("dataset_id") != resource_id:
            raise ApprovalConsumerError("snapshot dataset does not match resource")
        expected_acl_revision = _positive_revision(snapshot.get("expected_acl_revision"))
        if snapshot.get("current_acl_mode") != "dataset_acl":
            raise ApprovalConsumerError("snapshot ACL mode is not dataset_acl")
        request_id, request_ip = _request_evidence(payload, approval_request_id)

        # ``disable_dataset_acl`` performs the authoritative tenant/member,
        # Dataset mode and revision checks inside its own transaction.  The
        # approval execution id is stable for this request and therefore is the
        # deterministic ACL idempotency key for a retry-safe downstream call.
        result = disable_dataset_acl(
            mutation_engine_provider(),
            tenant_id=_text(payload, "tenant_id", maximum=64),
            dataset_id=resource_id,
            actor_id=actor_id,
            actor_role=actor_role,
            expected_acl_revision=expected_acl_revision,
            reason=reason,
            request_id=request_id,
            request_ip=request_ip,
            idempotency_key=execution_id,
            request_payload={
                "approval_request_id": approval_request_id,
                "requester_id": requester_id,
                "snapshot": dict(snapshot),
            },
            approval_execution_id=execution_id,
        )
        return _safe_acl_result(result)

    return consume


def _safe_member_result(value: Any) -> dict[str, Any]:
    """Keep only the stable membership/audit projection for replay storage."""

    value = _service_result_body(value, label="member role mutation")
    if not isinstance(value, Mapping):
        raise ApprovalConsumerError("member role mutation result is invalid")
    membership = value.get("membership")
    audit = value.get("audit")
    if not isinstance(membership, Mapping) or not isinstance(audit, Mapping):
        raise ApprovalConsumerError("member role mutation result is incomplete")
    account_id = membership.get("account_id")
    tenant_id = membership.get("tenant_id")
    role = membership.get("role")
    status = membership.get("status")
    revision = membership.get("revision")
    audit_id = audit.get("id")
    sequence = audit.get("sequence")
    if not all(
        isinstance(item, str) and item.strip()
        for item in (account_id, tenant_id, role, status, audit_id)
    ):
        raise ApprovalConsumerError("member role mutation result is invalid")
    if role.strip().casefold() not in _MEMBER_ROLES or type(revision) is not int or revision < 1:
        raise ApprovalConsumerError("member role mutation result role/revision is invalid")
    if type(sequence) is not int or sequence < 1:
        raise ApprovalConsumerError("member role mutation result audit is invalid")
    return {
        "membership": {
            "account_id": account_id.strip(),
            "tenant_id": tenant_id.strip(),
            "role": role.strip().casefold(),
            "status": status.strip().casefold(),
            "revision": revision,
        },
        "audit": {"id": audit_id.strip(), "sequence": sequence},
    }


def build_member_role_change_consumer(
    mutation_engine_provider: Callable[[], Any],
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build the approval consumer for tenant member role changes."""

    if not callable(mutation_engine_provider):
        raise TypeError("mutation_engine_provider must be callable")

    def consume(payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ApprovalConsumerError("approval execution payload must be an object")
        if _text(payload, "action_type", maximum=64) != ACTION_MEMBER_ROLE_CHANGE:
            raise ApprovalConsumerError("unsupported approval action")
        if _text(payload, "resource_type", maximum=64) != RESOURCE_TENANT_MEMBER:
            raise ApprovalConsumerError("unsupported approval resource type")
        tenant_id = _text(payload, "tenant_id", maximum=64)
        resource_id = _text(payload, "resource_id", maximum=64)
        approval_request_id = _text(payload, "approval_request_id", maximum=64)
        execution_id = _text(payload, "execution_id", maximum=128)
        _text(payload, "requester_id", maximum=64)
        actor_id, actor_role = _consumer_actor(payload)
        reason = _text(payload, "reason", maximum=512)
        snapshot = payload.get("request_snapshot") or payload.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise ApprovalConsumerError("request snapshot is required")
        if snapshot.get("target_account_id") != resource_id:
            raise ApprovalConsumerError("snapshot target account does not match resource")
        expected_revision = snapshot.get("expected_member_revision")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ApprovalConsumerError("expected_member_revision is invalid")
        current_role = snapshot.get("current_role")
        requested_role = snapshot.get("requested_role")
        if (
            not isinstance(current_role, str)
            or current_role.strip().casefold() not in _MEMBER_ROLES
            or not isinstance(requested_role, str)
            or requested_role.strip().casefold() not in _MEMBER_ROLES
            or current_role.strip().casefold() == requested_role.strip().casefold()
        ):
            raise ApprovalConsumerError("member role snapshot is invalid")
        if snapshot.get("current_status") != "active":
            raise ApprovalConsumerError("member role snapshot status is not active")
        request_id, request_ip = _request_evidence(payload, approval_request_id)
        result = change_tenant_member_role(
            mutation_engine_provider(),
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_role=actor_role,
            target_account_id=resource_id,
            expected_revision=expected_revision,
            role=requested_role.strip().casefold(),
            reason=reason,
            request_id=request_id,
            request_ip=request_ip,
            approval_execution_id=execution_id,
        )
        return _safe_member_result(result)

    return consume


def _transfer_snapshot_text(snapshot: Mapping[str, Any], *keys: str) -> str:
    values = [snapshot.get(key) for key in keys if snapshot.get(key) is not None]
    if not values or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ApprovalConsumerError("dataset workspace transfer snapshot binding is invalid")
    normalized = [value.strip() for value in values]
    if len(set(normalized)) != 1:
        raise ApprovalConsumerError("dataset workspace transfer snapshot binding is invalid")
    return normalized[0]


def _transfer_snapshot_revision(snapshot: Mapping[str, Any], *keys: str) -> int:
    values = [snapshot.get(key) for key in keys if snapshot.get(key) is not None]
    if not values or any(type(value) is not int or value < 1 for value in values):
        raise ApprovalConsumerError("dataset workspace transfer snapshot revision is invalid")
    if len(set(values)) != 1:
        raise ApprovalConsumerError("dataset workspace transfer snapshot revision is invalid")
    return values[0]


def _optional_transfer_snapshot_revision(snapshot: Mapping[str, Any], *keys: str) -> int | None:
    values = [snapshot.get(key) for key in keys if snapshot.get(key) is not None]
    if not values:
        return None
    if any(type(value) is not int or value < 1 for value in values) or len(set(values)) != 1:
        raise ApprovalConsumerError("dataset workspace transfer Workspace revision is invalid")
    return values[0]


def _transfer_snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _dataset_workspace_transfer_execution_fact(
    payload: Mapping[str, Any],
    *,
    tenant_id: str,
    approval_request_id: str,
    execution_id: str,
    execution_revision: int,
    resource_id: str,
    snapshot: Mapping[str, Any],
    reason: str,
) -> Any:
    from core.enterprise_approval_control import ApprovalExecutionFact

    fact = payload.get("approval_execution_fact")
    if not isinstance(fact, ApprovalExecutionFact):
        raise ApprovalConsumerError("internal approval execution fact is required")
    dataset_id = _transfer_snapshot_text(snapshot, "dataset_id")
    profile_revision = _transfer_snapshot_revision(
        snapshot, "profile_revision", "dataset_profile_revision", "expected_profile_revision"
    )
    ownership_revision = _transfer_snapshot_revision(
        snapshot, "ownership_revision", "expected_ownership_revision"
    )
    source_workspace_id = _transfer_snapshot_text(
        snapshot, "source_workspace_id", "from_workspace_id"
    )
    target_workspace_id = _transfer_snapshot_text(
        snapshot, "target_workspace_id", "to_workspace_id"
    )
    source_workspace_revision = _transfer_snapshot_revision(
        snapshot, "source_workspace_revision", "expected_source_workspace_revision"
    )
    target_workspace_revision = _transfer_snapshot_revision(
        snapshot, "target_workspace_revision", "expected_target_workspace_revision"
    )
    snapshot_reason = snapshot.get("reason")
    if (
        dataset_id != resource_id
        or source_workspace_id == target_workspace_id
        or not isinstance(snapshot_reason, str)
        or snapshot_reason.strip() != reason
    ):
        raise ApprovalConsumerError("dataset workspace transfer snapshot scope is invalid")
    expected_hash = _transfer_snapshot_hash(snapshot)
    if (
        fact.tenant_id != tenant_id
        or fact.approval_request_id != approval_request_id
        or fact.execution_id != execution_id
        or type(fact.request_revision) is not int
        or fact.request_revision < 1
        or type(fact.execution_revision) is not int
        or fact.execution_revision != fact.request_revision + 1
        or fact.execution_revision != execution_revision
        or fact.action_type != ACTION_DATASET_WORKSPACE_TRANSFER
        or fact.resource_type != RESOURCE_KNOWLEDGE_BASE
        or fact.resource_id != resource_id
        or fact.reason != reason
        or not isinstance(fact.snapshot_hash, str)
        or not _SHA256_HEX_RE.fullmatch(fact.snapshot_hash.strip().casefold())
        or fact.snapshot_hash.strip().casefold() != expected_hash
    ):
        raise ApprovalConsumerError("internal approval execution fact does not match request")

    fact_profile_revisions = [
        value
        for value in (
            fact.profile_revision,
            fact.dataset_profile_revision,
            fact.expected_dataset_profile_revision,
        )
        if value is not None
    ]
    fact_ownership_revisions = [
        value
        for value in (fact.ownership_revision, fact.expected_ownership_revision)
        if value is not None
    ]
    if (
        not fact_profile_revisions
        or len(set(fact_profile_revisions)) != 1
        or not fact_ownership_revisions
        or len(set(fact_ownership_revisions)) != 1
    ):
        raise ApprovalConsumerError("internal approval execution fact does not match request")
    if (
        fact_profile_revisions[0] != profile_revision
        or fact_ownership_revisions[0] != ownership_revision
        or fact.source_workspace_id != source_workspace_id
        or fact.target_workspace_id != target_workspace_id
        or fact.source_workspace_revision != source_workspace_revision
        or fact.target_workspace_revision != target_workspace_revision
        or fact.expected_source_workspace_revision != source_workspace_revision
        or fact.expected_target_workspace_revision != target_workspace_revision
    ):
        raise ApprovalConsumerError("internal approval execution fact does not match request")
    return fact


def _load_dataset_workspace_transfer_service() -> Callable[..., Any]:
    try:
        module = importlib.import_module("core.enterprise_knowledge_base_registry")
    except ModuleNotFoundError as exc:
        if exc.name != "core.enterprise_knowledge_base_registry":
            raise ApprovalConsumerError(
                "dataset workspace transfer service is not connected"
            ) from exc
        raise ApprovalConsumerError("dataset workspace transfer service is not connected") from exc
    service = getattr(module, "transfer_dataset_ownership", None)
    if not callable(service):
        raise ApprovalConsumerError("dataset workspace transfer service is not connected")
    return service


def _safe_transfer_result(
    value: Any,
    *,
    expected_tenant_id: str | None = None,
    expected_dataset_id: str | None = None,
    expected_source_workspace_id: str | None = None,
    expected_target_workspace_id: str | None = None,
    expected_source_workspace_revision: int | None = None,
    expected_target_workspace_revision: int | None = None,
) -> dict[str, Any]:
    """Whitelist stable Dataset ownership transfer facts for replay storage."""

    value = _service_result_body(value, label="dataset workspace transfer")
    if not isinstance(value, Mapping):
        raise ApprovalConsumerError("dataset workspace transfer result is invalid")
    dataset = value.get("dataset")
    if not isinstance(dataset, Mapping):
        raise ApprovalConsumerError("dataset workspace transfer result is incomplete")
    result_dataset_id = dataset.get("id")
    result_tenant_id = dataset.get("tenant_id")
    profile_revision = dataset.get("profile_revision")
    if (
        not isinstance(result_dataset_id, str)
        or not result_dataset_id.strip()
        or not isinstance(result_tenant_id, str)
        or not result_tenant_id.strip()
        or type(profile_revision) is not int
        or profile_revision < 1
    ):
        raise ApprovalConsumerError("dataset workspace transfer dataset result is invalid")
    if (expected_tenant_id is not None and result_tenant_id != expected_tenant_id) or (
        expected_dataset_id is not None and result_dataset_id != expected_dataset_id
    ):
        raise ApprovalConsumerError(
            "dataset workspace transfer result does not match approval scope"
        )

    def project(item: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        return {key: item[key] for key in keys if key in item}

    result: dict[str, Any] = {
        "dataset": project(
            dataset,
            ("id", "tenant_id", "name", "description", "status", "profile_revision"),
        )
    }
    ownership = value.get("ownership") or value.get("dataset_workspace_ownership")
    if not isinstance(ownership, Mapping):
        raise ApprovalConsumerError("dataset workspace transfer ownership result is missing")
    if (
        ownership.get("dataset_id") != result_dataset_id
        or ownership.get("tenant_id") != result_tenant_id
        or not isinstance(ownership.get("workspace_id"), str)
        or not ownership.get("workspace_id", "").strip()
        or type(ownership.get("revision")) is not int
        or ownership.get("revision") < 1
    ):
        raise ApprovalConsumerError("dataset workspace transfer ownership result is invalid")
    if (
        expected_target_workspace_id is not None
        and ownership.get("workspace_id") != expected_target_workspace_id
    ):
        raise ApprovalConsumerError(
            "dataset workspace transfer result does not match approval scope"
        )
    result["ownership"] = project(
        ownership,
        (
            "id",
            "tenant_id",
            "dataset_id",
            "workspace_id",
            "revision",
            "last_transfer_at",
        ),
    )

    for key in ("source_workspace", "target_workspace"):
        item = value.get(key)
        if item is None:
            continue
        if not isinstance(item, Mapping):
            raise ApprovalConsumerError("dataset workspace transfer Workspace result is invalid")
        workspace_id = item.get("id")
        workspace_tenant = item.get("tenant_id")
        workspace_status = item.get("status")
        workspace_revision = item.get("revision")
        if (
            not isinstance(workspace_id, str)
            or not workspace_id.strip()
            or workspace_tenant != result_tenant_id
            or not isinstance(workspace_status, str)
            or not workspace_status.strip()
            or type(workspace_revision) is not int
            or workspace_revision < 1
        ):
            raise ApprovalConsumerError("dataset workspace transfer Workspace result is invalid")
        expected_workspace_id = (
            expected_source_workspace_id
            if key == "source_workspace"
            else expected_target_workspace_id
        )
        if expected_workspace_id is not None and workspace_id != expected_workspace_id:
            raise ApprovalConsumerError(
                "dataset workspace transfer result does not match approval scope"
            )
        expected_workspace_revision = (
            expected_source_workspace_revision
            if key == "source_workspace"
            else expected_target_workspace_revision
        )
        if (
            expected_workspace_revision is not None
            and workspace_revision != expected_workspace_revision
        ):
            raise ApprovalConsumerError(
                "dataset workspace transfer result Workspace revision does not match approval scope"
            )
        result[key] = project(item, ("id", "tenant_id", "name", "status", "revision"))

    bindings = value.get("bindings")
    if bindings is not None:
        if not isinstance(bindings, list):
            raise ApprovalConsumerError("dataset workspace transfer bindings result is invalid")
        safe_bindings: list[dict[str, Any]] = []
        for binding in bindings:
            if not isinstance(binding, Mapping):
                raise ApprovalConsumerError("dataset workspace transfer binding result is invalid")
            if (
                type(binding.get("id")) is not int
                or binding.get("id") < 1
                or binding.get("tenant_id") != result_tenant_id
                or binding.get("dataset_id") != result_dataset_id
                or not isinstance(binding.get("workspace_id"), str)
                or not binding.get("workspace_id", "").strip()
                or not isinstance(binding.get("binding_kind"), str)
                or not binding.get("binding_kind", "").strip()
                or not isinstance(binding.get("status"), str)
                or not binding.get("status", "").strip()
                or type(binding.get("revision")) is not int
                or binding.get("revision") < 1
            ):
                raise ApprovalConsumerError("dataset workspace transfer binding result is invalid")
            safe_bindings.append(
                project(
                    binding,
                    (
                        "id",
                        "tenant_id",
                        "workspace_id",
                        "workspace_name",
                        "workspace_status",
                        "dataset_id",
                        "binding_kind",
                        "active_primary_slot",
                        "status",
                        "revision",
                    ),
                )
            )
        result["bindings"] = safe_bindings

    mutation = value.get("mutation")
    if mutation is not None:
        if (
            not isinstance(mutation, Mapping)
            or mutation.get("resource_id") != result_dataset_id
            or not isinstance(mutation.get("result"), str)
            or not mutation.get("result", "").strip()
        ):
            raise ApprovalConsumerError("dataset workspace transfer mutation result is invalid")
        result["mutation"] = project(mutation, ("result", "resource_id"))

    audit = value.get("audit")
    if audit is not None:
        if (
            not isinstance(audit, Mapping)
            or not isinstance(audit.get("id"), str)
            or not audit.get("id", "").strip()
            or type(audit.get("sequence")) is not int
            or audit.get("sequence") < 1
        ):
            raise ApprovalConsumerError("dataset workspace transfer audit result is invalid")
        result["audit"] = project(audit, ("id", "sequence"))
    return result


def build_dataset_workspace_transfer_consumer(
    mutation_engine_provider: Callable[[], Any],
    *,
    transfer_service: Callable[..., Any] | None = None,
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build the one-time-ticket consumer for Dataset Workspace ownership transfer."""

    if not callable(mutation_engine_provider):
        raise TypeError("mutation_engine_provider must be callable")
    if transfer_service is not None and not callable(transfer_service):
        raise TypeError("transfer_service must be callable")

    def consume(payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ApprovalConsumerError("approval execution payload must be an object")
        if _text(payload, "action_type", maximum=64) != ACTION_DATASET_WORKSPACE_TRANSFER:
            raise ApprovalConsumerError("unsupported approval action")
        if _text(payload, "resource_type", maximum=64) != RESOURCE_KNOWLEDGE_BASE:
            raise ApprovalConsumerError("unsupported approval resource type")
        tenant_id = _text(payload, "tenant_id", maximum=64)
        resource_id = _text(payload, "resource_id", maximum=256)
        approval_request_id = _text(payload, "approval_request_id", maximum=64)
        execution_id = _text(payload, "execution_id", maximum=128)
        execution_revision = _positive_named_revision(
            payload.get("execution_revision"), "execution_revision"
        )
        _text(payload, "requester_id", maximum=64)
        actor_id, actor_role = _consumer_actor(payload)
        reason = _text(payload, "reason", maximum=512)
        snapshot = payload.get("request_snapshot") or payload.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise ApprovalConsumerError("request snapshot is required")
        if payload.get("request_snapshot") is not None and payload.get("snapshot") is not None:
            if payload["request_snapshot"] != payload["snapshot"]:
                raise ApprovalConsumerError("request snapshots do not match")
        execution_fact = _dataset_workspace_transfer_execution_fact(
            payload,
            tenant_id=tenant_id,
            approval_request_id=approval_request_id,
            execution_id=execution_id,
            execution_revision=execution_revision,
            resource_id=resource_id,
            snapshot=snapshot,
            reason=reason,
        )
        profile_revision = next(
            (
                value
                for value in (
                    execution_fact.profile_revision,
                    execution_fact.dataset_profile_revision,
                    execution_fact.expected_dataset_profile_revision,
                )
                if value is not None
            ),
            None,
        )
        ownership_revision = next(
            (
                value
                for value in (
                    execution_fact.ownership_revision,
                    execution_fact.expected_ownership_revision,
                )
                if value is not None
            ),
            None,
        )
        source_workspace_revision = execution_fact.source_workspace_revision
        target_workspace_revision = execution_fact.target_workspace_revision
        if (
            profile_revision is None
            or ownership_revision is None
            or type(source_workspace_revision) is not int
            or type(target_workspace_revision) is not int
            or source_workspace_revision < 1
            or target_workspace_revision < 1
        ):
            raise ApprovalConsumerError("internal approval execution fact revisions are invalid")
        request_id, request_ip = _request_evidence(payload, approval_request_id)
        service = transfer_service or _load_dataset_workspace_transfer_service()
        result = service(
            mutation_engine_provider(),
            tenant_id=tenant_id,
            dataset_id=resource_id,
            actor_id=actor_id,
            actor_role=actor_role,
            target_workspace_id=execution_fact.target_workspace_id,
            expected_dataset_profile_revision=profile_revision,
            expected_ownership_revision=ownership_revision,
            expected_source_workspace_revision=source_workspace_revision,
            expected_target_workspace_revision=target_workspace_revision,
            reason=reason,
            request_id=request_id,
            request_ip=request_ip,
            idempotency_key=execution_id,
            approval_execution_fact=execution_fact,
        )
        return _safe_transfer_result(
            result,
            expected_tenant_id=tenant_id,
            expected_dataset_id=resource_id,
            expected_source_workspace_id=execution_fact.source_workspace_id,
            expected_target_workspace_id=execution_fact.target_workspace_id,
            expected_source_workspace_revision=source_workspace_revision,
            expected_target_workspace_revision=target_workspace_revision,
        )

    return consume


def _positive_named_revision(value: Any, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ApprovalConsumerError(f"{field} is invalid")
    return value


def _workspace_authorization_mode(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ApprovalConsumerError(f"{field} is invalid")
    result = value.strip().casefold()
    if result not in _WORKSPACE_AUTHORIZATION_MODES:
        raise ApprovalConsumerError(f"{field} is invalid")
    return result


def _workspace_execution_fact(
    payload: Mapping[str, Any],
    *,
    tenant_id: str,
    approval_request_id: str,
    execution_id: str,
    execution_revision: int,
    workspace_id: str,
    workspace_revision: int,
    policy_revision: int,
    from_mode: str,
    target_mode: str,
    model_version: int,
    fingerprint: str,
    reason: str,
) -> Any:
    from core.enterprise_approval_control import ApprovalExecutionFact

    fact = payload.get("approval_execution_fact")
    if not isinstance(fact, ApprovalExecutionFact):
        raise ApprovalConsumerError("internal approval execution fact is required")
    if (
        fact.tenant_id != tenant_id
        or fact.approval_request_id != approval_request_id
        or fact.execution_id != execution_id
        or type(fact.request_revision) is not int
        or type(fact.execution_revision) is not int
        or fact.request_revision < 1
        or fact.execution_revision < 1
        or fact.execution_revision != fact.request_revision + 1
        or fact.execution_revision != execution_revision
        or fact.action_type != ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE
        or fact.resource_type != RESOURCE_TENANT_WORKSPACE
        or fact.resource_id != workspace_id
        or fact.workspace_revision != workspace_revision
        or fact.policy_revision != policy_revision
        or fact.from_mode != from_mode
        or fact.target_mode != target_mode
        or fact.permission_model_version != model_version
        or fact.permission_matrix_fingerprint != fingerprint
        or fact.reason != reason
        or not isinstance(fact.snapshot_hash, str)
        or not _SHA256_HEX_RE.fullmatch(fact.snapshot_hash.strip().casefold())
    ):
        raise ApprovalConsumerError("internal approval execution fact does not match request")
    return fact


def _resolve_workspace_authorization_live_mode(
    engine: Any, *, tenant_id: str, actor_id: str, workspace_id: str
) -> str:
    module = importlib.import_module("core.enterprise_workspace_authorization")
    result = module.get_workspace_authorization_policy(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        workspace_id=workspace_id,
    )
    body = getattr(result, "body", None)
    policy = body.get("policy") if isinstance(body, Mapping) else None
    mode = policy.get("mode") if isinstance(policy, Mapping) else None
    if not isinstance(mode, str) or mode.strip().casefold() not in _WORKSPACE_AUTHORIZATION_MODES:
        raise ApprovalConsumerError("workspace authorization live mode is unavailable")
    return mode.strip().casefold()


def _load_workspace_authorization_mode_change_service() -> Callable[..., Any]:
    """Resolve the Stage 17 Core lazily during rolling worker integration."""

    candidates = (
        ("core.enterprise_workspace_authorization", "change_workspace_authorization_mode"),
        ("core.enterprise_workspace_control", "change_workspace_authorization_mode"),
    )
    for module_name, function_name in candidates:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise ApprovalConsumerError(
                    "workspace authorization mode change service is unavailable"
                ) from exc
            continue
        service = getattr(module, function_name, None)
        if callable(service):
            return service
    raise ApprovalConsumerError("workspace authorization mode change service is not connected")


def _safe_workspace_authorization_result(
    value: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    target_mode: str,
) -> dict[str, Any]:
    """Whitelist stable policy, Workspace and audit facts for replay storage."""

    if not isinstance(value, Mapping):
        raise ApprovalConsumerError("workspace authorization mutation result is invalid")
    policy = value.get("authorization_policy") or value.get("policy")
    workspace = value.get("workspace")
    audit = value.get("audit")
    if not isinstance(policy, Mapping) or not isinstance(workspace, Mapping):
        raise ApprovalConsumerError("workspace authorization mutation result is incomplete")
    if not isinstance(audit, Mapping):
        raise ApprovalConsumerError("workspace authorization mutation audit is missing")

    policy_id = policy.get("id")
    policy_tenant = policy.get("tenant_id")
    policy_workspace = policy.get("workspace_id")
    mode = policy.get("mode")
    model_version = policy.get("permission_model_version")
    policy_revision = policy.get("revision")
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise ApprovalConsumerError("workspace authorization policy id is invalid")
    if policy_tenant != tenant_id or policy_workspace != workspace_id:
        raise ApprovalConsumerError("workspace authorization policy scope is invalid")
    if not isinstance(mode, str) or mode.strip().casefold() != target_mode:
        raise ApprovalConsumerError("workspace authorization policy mode is invalid")
    if type(model_version) is not int or model_version != 1:
        raise ApprovalConsumerError("workspace authorization permission model is invalid")
    if type(policy_revision) is not int or policy_revision < 1:
        raise ApprovalConsumerError("workspace authorization policy revision is invalid")

    result_workspace_id = workspace.get("id")
    workspace_tenant = workspace.get("tenant_id")
    workspace_status = workspace.get("status")
    workspace_revision = workspace.get("revision")
    if result_workspace_id != workspace_id or workspace_tenant != tenant_id:
        raise ApprovalConsumerError("workspace authorization Workspace scope is invalid")
    if not isinstance(workspace_status, str) or workspace_status.strip().casefold() not in {
        "active",
        "archived",
    }:
        raise ApprovalConsumerError("workspace authorization Workspace status is invalid")
    normalized_workspace_status = workspace_status.strip().casefold()
    if target_mode in {"shadow", "enforced"} and normalized_workspace_status != "active":
        raise ApprovalConsumerError("workspace authorization Workspace is not active")
    if type(workspace_revision) is not int or workspace_revision < 1:
        raise ApprovalConsumerError("workspace authorization Workspace revision is invalid")

    audit_id = audit.get("id")
    audit_sequence = audit.get("sequence")
    if not isinstance(audit_id, str) or not audit_id.strip():
        raise ApprovalConsumerError("workspace authorization audit id is invalid")
    if type(audit_sequence) is not int or audit_sequence < 1:
        raise ApprovalConsumerError("workspace authorization audit sequence is invalid")

    return {
        "authorization_policy": {
            "id": policy_id.strip(),
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "mode": target_mode,
            "permission_model_version": model_version,
            "revision": policy_revision,
        },
        "workspace": {
            "id": workspace_id,
            "tenant_id": tenant_id,
            "status": normalized_workspace_status,
            "revision": workspace_revision,
        },
        "audit": {"id": audit_id.strip(), "sequence": audit_sequence},
    }


def build_workspace_authorization_mode_change_consumer(
    mutation_engine_provider: Callable[[], Any],
    *,
    mode_change_service: Callable[..., Any] | None = None,
    live_mode_resolver: Callable[..., str] | None = None,
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build the one-time-ticket consumer for Workspace authorization rollout."""

    if not callable(mutation_engine_provider):
        raise TypeError("mutation_engine_provider must be callable")
    if mode_change_service is not None and not callable(mode_change_service):
        raise TypeError("mode_change_service must be callable")
    if live_mode_resolver is not None and not callable(live_mode_resolver):
        raise TypeError("live_mode_resolver must be callable")

    def consume(payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ApprovalConsumerError("approval execution payload must be an object")
        if _text(payload, "action_type", maximum=64) != ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE:
            raise ApprovalConsumerError("unsupported approval action")
        if _text(payload, "resource_type", maximum=64) != RESOURCE_TENANT_WORKSPACE:
            raise ApprovalConsumerError("unsupported approval resource type")

        tenant_id = _text(payload, "tenant_id", maximum=64)
        workspace_id = _text(payload, "resource_id", maximum=128)
        approval_request_id = _text(payload, "approval_request_id", maximum=64)
        execution_id = _text(payload, "execution_id", maximum=128)
        execution_revision = _positive_named_revision(
            payload.get("execution_revision"), "execution_revision"
        )
        _text(payload, "requester_id", maximum=64)
        actor_id, actor_role = _consumer_actor(payload)
        reason = _text(payload, "reason", maximum=512)
        snapshot = payload.get("request_snapshot") or payload.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise ApprovalConsumerError("request snapshot is required")
        if snapshot.get("workspace_id") != workspace_id:
            raise ApprovalConsumerError("snapshot workspace does not match resource")

        workspace_revision = _positive_named_revision(
            snapshot.get("workspace_revision"), "workspace_revision"
        )
        policy_revision = _positive_named_revision(
            snapshot.get("policy_revision"), "policy_revision"
        )
        from_mode = _workspace_authorization_mode(snapshot.get("from_mode"), "from_mode")
        target_mode = _workspace_authorization_mode(snapshot.get("target_mode"), "target_mode")
        if from_mode == target_mode:
            raise ApprovalConsumerError("mode transition is invalid")
        model_version = snapshot.get("permission_model_version")
        if type(model_version) is not int or model_version != 1:
            raise ApprovalConsumerError("permission_model_version is unsupported")
        fingerprint = snapshot.get("permission_matrix_fingerprint")
        if not isinstance(fingerprint, str) or not _SHA256_HEX_RE.fullmatch(
            fingerprint.strip().casefold()
        ):
            raise ApprovalConsumerError("permission_matrix_fingerprint is invalid")
        normalized_fingerprint = fingerprint.strip().casefold()
        snapshot_reason = snapshot.get("reason")
        if not isinstance(snapshot_reason, str) or snapshot_reason.strip() != reason:
            raise ApprovalConsumerError("snapshot reason does not match request reason")

        execution_fact = _workspace_execution_fact(
            payload,
            tenant_id=tenant_id,
            approval_request_id=approval_request_id,
            execution_id=execution_id,
            execution_revision=execution_revision,
            workspace_id=workspace_id,
            workspace_revision=workspace_revision,
            policy_revision=policy_revision,
            from_mode=from_mode,
            target_mode=target_mode,
            model_version=model_version,
            fingerprint=normalized_fingerprint,
            reason=reason,
        )
        request_id, request_ip = _request_evidence(payload, approval_request_id)
        mutation_engine = mutation_engine_provider()
        resolver = live_mode_resolver or _resolve_workspace_authorization_live_mode
        live_mode = resolver(
            mutation_engine,
            tenant_id=tenant_id,
            actor_id=actor_id,
            workspace_id=workspace_id,
        )
        if not isinstance(live_mode, str) or live_mode.strip().casefold() != from_mode:
            raise ApprovalConsumerError("approval snapshot from_mode does not match live mode")
        service = mode_change_service or _load_workspace_authorization_mode_change_service()
        result = service(
            mutation_engine,
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_role=actor_role,
            workspace_id=workspace_id,
            expected_workspace_revision=workspace_revision,
            expected_policy_revision=policy_revision,
            expected_from_mode=from_mode,
            target_mode=target_mode,
            expected_permission_model_version=model_version,
            permission_matrix_fingerprint=normalized_fingerprint,
            reason=reason,
            request_id=request_id,
            request_ip=request_ip,
            idempotency_key=execution_id,
            approval_execution_fact=execution_fact,
        )
        return _safe_workspace_authorization_result(
            result,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            target_mode=target_mode,
        )

    return consume


def _safe_release_result(value: Any, *, operation: str) -> dict[str, Any]:
    value = _service_result_body(value, label=f"release {operation}")
    if value.get("state") != "applied" or value.get("operation") != operation:
        raise ApprovalConsumerError(f"release {operation} result is invalid")
    resource_id = value.get("resource_id")
    revision = value.get("revision")
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ApprovalConsumerError(f"release {operation} result resource is invalid")
    if type(revision) is not int or revision < 1:
        raise ApprovalConsumerError(f"release {operation} result revision is invalid")
    safe: dict[str, Any] = {
        "state": "applied",
        "operation": operation,
        "resource_id": resource_id.strip(),
        "revision": revision,
    }
    for key in ("binding", "serving", "channel", "release"):
        candidate = value.get(key)
        if isinstance(candidate, Mapping):
            safe[key] = dict(candidate)
    rendered = json.dumps(safe, ensure_ascii=False, sort_keys=True)
    if re.search(
        r"(?i)(?:ticket|idempotency[_-]?key|password|secret|access[_-]?token|authorization)",
        rendered,
    ):
        raise ApprovalConsumerError(f"release {operation} result contains unsafe fields")
    return safe


def _load_release_service(operation: str) -> Callable[..., Any]:
    module = importlib.import_module("core.enterprise_knowledge_base_releases")
    service_name = {
        "promote": "promote_release",
        "rollback": "rollback_channel_release",
    }.get(operation)
    service = getattr(module, service_name, None) if service_name is not None else None
    if not callable(service):
        raise ApprovalConsumerError(f"release {operation} service is unavailable")
    return service


def _release_execution_fact(payload: Mapping[str, Any], *, action_type: str) -> Any:
    from core.enterprise_approval_control import ApprovalExecutionFact

    fact = payload.get("approval_execution_fact")
    if not isinstance(fact, ApprovalExecutionFact):
        raise ApprovalConsumerError("approval execution fact must be opaque internal authority")
    if fact.action_type != action_type:
        raise ApprovalConsumerError("approval execution fact action mismatch")
    return fact


def _build_knowledge_base_release_consumer(
    mutation_engine_provider: Callable[[], Any],
    *,
    action_type: str,
    operation: str,
    service: Callable[..., Any] | None = None,
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    if not callable(mutation_engine_provider):
        raise TypeError("mutation_engine_provider must be callable")
    if service is not None and not callable(service):
        raise TypeError("service must be callable")

    def consume(payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ApprovalConsumerError("approval execution payload must be an object")
        if _text(payload, "action_type", maximum=64) != action_type:
            raise ApprovalConsumerError("unsupported approval action")
        if _text(payload, "resource_type", maximum=64) != RESOURCE_KNOWLEDGE_BASE:
            raise ApprovalConsumerError("unsupported approval resource type")
        tenant_id = _text(payload, "tenant_id", maximum=64)
        dataset_id = _text(payload, "resource_id", maximum=64)
        approval_request_id = _text(payload, "approval_request_id", maximum=64)
        execution_id = _text(payload, "execution_id", maximum=128)
        actor_id, _actor_role = _consumer_actor(payload)
        reason = _text(payload, "reason", maximum=512)
        snapshot = payload.get("request_snapshot") or payload.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise ApprovalConsumerError("request snapshot is required")
        fact = _release_execution_fact(payload, action_type=action_type)
        if (
            fact.tenant_id != tenant_id
            or fact.approval_request_id != approval_request_id
            or fact.execution_id != execution_id
            or fact.resource_type != RESOURCE_KNOWLEDGE_BASE
            or fact.resource_id != dataset_id
            or snapshot.get("dataset_id") != dataset_id
            or snapshot.get("action_type") != action_type
            or snapshot.get("release_id") != fact.release_id
            or snapshot.get("manifest_digest") != fact.manifest_digest
            or snapshot.get("channel_id") != fact.channel_id
            or snapshot.get("channel_revision") != fact.channel_revision
            or snapshot.get("profile_revision") != fact.profile_revision
            or snapshot.get("mutation_generation") != fact.mutation_generation
            or snapshot.get("ownership_revision") != fact.ownership_revision
            or snapshot.get("workspace_id") != fact.workspace_id
            or snapshot.get("workspace_revision") != fact.workspace_revision
            or snapshot.get("serving_generation") != fact.serving_generation
            or snapshot.get("reason") != reason
        ):
            raise ApprovalConsumerError("release approval snapshot does not match execution fact")
        request_id, request_ip = _request_evidence(payload, approval_request_id)
        mutation_engine = mutation_engine_provider()
        target = service or _load_release_service(operation)
        if operation == "promote":
            result = target(
                mutation_engine,
                tenant_id=tenant_id,
                actor_id=actor_id,
                dataset_id=dataset_id,
                release_id=fact.release_id,
                channel_id=fact.channel_id,
                expected_channel_revision=fact.channel_revision,
                expected_profile_revision=fact.profile_revision,
                expected_ownership_revision=fact.ownership_revision,
                expected_workspace_revision=fact.workspace_revision,
                expected_serving_generation=fact.serving_generation,
                reason=reason,
                request_id=request_id,
                request_ip=request_ip,
                idempotency_key=execution_id,
                approval_execution_fact=fact,
            )
        else:
            result = target(
                mutation_engine,
                tenant_id=tenant_id,
                actor_id=actor_id,
                dataset_id=dataset_id,
                channel_id=fact.channel_id,
                target_release_id=fact.release_id,
                expected_channel_revision=fact.channel_revision,
                expected_serving_generation=fact.serving_generation,
                reason=reason,
                request_id=request_id,
                request_ip=request_ip,
                idempotency_key=execution_id,
                approval_execution_fact=fact,
            )
        return _safe_release_result(result, operation=operation)

    return consume


def _quality_waiver_execution_fact(payload: Mapping[str, Any]) -> Any:
    from core.enterprise_approval_control import ApprovalExecutionFact

    fact = payload.get("approval_execution_fact")
    if not isinstance(fact, ApprovalExecutionFact):
        raise ApprovalConsumerError("approval execution fact must be opaque internal authority")
    if fact.action_type != ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER:
        raise ApprovalConsumerError("approval execution fact action mismatch")
    return fact


def _safe_quality_waiver_result(value: Any) -> dict[str, Any]:
    value = _service_result_body(value, label="quality waiver")
    if value.get("state") != "applied" or value.get("operation") != "release_quality_waiver":
        raise ApprovalConsumerError("quality waiver result is invalid")
    resource_id = value.get("resource_id")
    waiver = value.get("waiver")
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ApprovalConsumerError("quality waiver result resource is invalid")
    if not isinstance(waiver, Mapping) or waiver.get("id") != resource_id:
        raise ApprovalConsumerError("quality waiver result is incomplete")
    required_text = (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "policy_id",
        "release_manifest_digest",
        "approval_request_id",
        "approval_execution_id",
        "reason",
        "valid_from",
        "expires_at",
        "waiver_digest",
        "created_at",
        "created_by",
        "request_id",
    )
    if any(
        not isinstance(waiver.get(key), str) or not waiver.get(key).strip() for key in required_text
    ):
        raise ApprovalConsumerError("quality waiver result contains invalid authority")
    if type(waiver.get("policy_revision")) is not int or waiver["policy_revision"] < 1:
        raise ApprovalConsumerError("quality waiver result policy revision is invalid")
    for field in ("release_manifest_digest", "waiver_digest"):
        if not _SHA256_HEX_RE.fullmatch(waiver[field].casefold()):
            raise ApprovalConsumerError("quality waiver result digest is invalid")
    safe_waiver = {key: waiver[key] for key in required_text}
    safe_waiver["policy_revision"] = waiver["policy_revision"]
    event = value.get("event")
    safe: dict[str, Any] = {
        "state": "applied",
        "operation": "release_quality_waiver",
        "resource_id": resource_id.strip(),
        "waiver": safe_waiver,
    }
    if not isinstance(event, Mapping):
        raise ApprovalConsumerError("quality waiver result event is missing")
    event_id = event.get("id")
    sequence = event.get("sequence")
    digest = event.get("event_digest")
    if (
        not isinstance(event_id, str)
        or not event_id.strip()
        or type(sequence) is not int
        or sequence < 1
        or not isinstance(digest, str)
        or not _SHA256_HEX_RE.fullmatch(digest.casefold())
    ):
        raise ApprovalConsumerError("quality waiver result event is invalid")
    safe["event"] = {
        "id": event_id.strip(),
        "sequence": sequence,
        "event_digest": digest.casefold(),
    }
    rendered = json.dumps(safe, ensure_ascii=False, sort_keys=True)
    if re.search(
        r"(?i)(?:ticket|idempotency[_-]?key|password|secret|access[_-]?token|authorization)",
        rendered,
    ):
        raise ApprovalConsumerError("quality waiver result contains unsafe fields")
    return safe


def build_knowledge_base_release_quality_waiver_consumer(
    mutation_engine_provider: Callable[[], Any],
    *,
    service: Callable[..., Any] | None = None,
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    if not callable(mutation_engine_provider):
        raise TypeError("mutation_engine_provider must be callable")
    if service is not None and not callable(service):
        raise TypeError("service must be callable")

    def consume(payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ApprovalConsumerError("approval execution payload must be an object")
        if (
            _text(payload, "action_type", maximum=64)
            != ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER
        ):
            raise ApprovalConsumerError("unsupported approval action")
        if _text(payload, "resource_type", maximum=64) != RESOURCE_KNOWLEDGE_BASE:
            raise ApprovalConsumerError("unsupported approval resource type")
        tenant_id = _text(payload, "tenant_id", maximum=64)
        dataset_id = _text(payload, "resource_id", maximum=64)
        approval_request_id = _text(payload, "approval_request_id", maximum=64)
        execution_id = _text(payload, "execution_id", maximum=128)
        actor_id, _actor_role = _consumer_actor(payload)
        reason = _text(payload, "reason", maximum=512)
        snapshot = payload.get("request_snapshot")
        if snapshot is None:
            snapshot = payload.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise ApprovalConsumerError("request snapshot is required")
        if payload.get("request_snapshot") is not None and payload.get("snapshot") is not None:
            if payload["request_snapshot"] != payload["snapshot"]:
                raise ApprovalConsumerError("request snapshots do not match")
        fact = _quality_waiver_execution_fact(payload)
        if (
            fact.tenant_id != tenant_id
            or fact.approval_request_id != approval_request_id
            or fact.execution_id != execution_id
            or fact.resource_type != RESOURCE_KNOWLEDGE_BASE
            or fact.resource_id != dataset_id
        ):
            raise ApprovalConsumerError(
                "quality waiver execution fact scope does not match payload"
            )
        expected = {
            "tenant_id": fact.tenant_id,
            "action_type": fact.action_type,
            "resource_type": fact.resource_type,
            "resource_id": fact.resource_id,
            "dataset_id": fact.resource_id,
            "release_id": fact.release_id,
            "release_number": fact.release_number,
            "manifest_digest": fact.manifest_digest,
            "channel_id": fact.channel_id,
            "channel_revision": fact.channel_revision,
            "quality_gate_revision": fact.quality_gate_revision,
            "policy_id": fact.policy_id,
            "policy_revision": fact.policy_revision,
            "policy_digest": fact.policy_digest,
            "quality_gate_digest": fact.quality_gate_digest,
            "profile_revision": fact.profile_revision,
            "mutation_generation": fact.mutation_generation,
            "ownership_revision": fact.ownership_revision,
            "workspace_id": fact.workspace_id,
            "workspace_revision": fact.workspace_revision,
            "serving_generation": fact.serving_generation,
            "reason": reason,
        }
        for field, value in expected.items():
            if value is not None and snapshot.get(field) != value:
                raise ApprovalConsumerError("quality waiver snapshot does not match execution fact")
        evidence_digest = fact.quality_evidence_digest or fact.evidence_digest
        if snapshot.get("quality_evidence_digest") != evidence_digest:
            raise ApprovalConsumerError("quality waiver evidence does not match execution fact")
        expiry = fact.waiver_expires_at or fact.requested_expires_at
        if (
            snapshot.get("requested_expires_at") != expiry
            or snapshot.get("waiver_expires_at") != expiry
        ):
            raise ApprovalConsumerError("quality waiver expiry does not match execution fact")
        if (
            fact.quality_gate_state is not None
            and snapshot.get("quality_gate_state") != fact.quality_gate_state
        ):
            raise ApprovalConsumerError("quality waiver gate state does not match execution fact")
        if (
            fact.quality_gate_reason is not None
            and snapshot.get("quality_gate_reason") != fact.quality_gate_reason
        ):
            raise ApprovalConsumerError("quality waiver gate reason does not match execution fact")
        request_id, request_ip = _request_evidence(payload, approval_request_id)
        target = service
        if target is None:
            module = importlib.import_module("core.enterprise_release_quality_waivers")
            target = getattr(module, "grant_quality_waiver", None)
        if not callable(target):
            raise ApprovalConsumerError("quality waiver service is unavailable")
        result = target(
            mutation_engine_provider(),
            tenant_id=tenant_id,
            actor_id=actor_id,
            dataset_id=dataset_id,
            release_id=fact.release_id,
            channel_id=fact.channel_id,
            reason=reason,
            request_id=request_id,
            request_ip=request_ip,
            idempotency_key=execution_id,
            now=payload.get("execution_now"),
            approval_execution_fact=fact,
        )
        return _safe_quality_waiver_result(result)

    return consume


def build_knowledge_base_release_publish_consumer(
    mutation_engine_provider: Callable[[], Any],
    *,
    service: Callable[..., Any] | None = None,
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    return _build_knowledge_base_release_consumer(
        mutation_engine_provider,
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
        operation="promote",
        service=service,
    )


def build_knowledge_base_release_rollback_consumer(
    mutation_engine_provider: Callable[[], Any],
    *,
    service: Callable[..., Any] | None = None,
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    return _build_knowledge_base_release_consumer(
        mutation_engine_provider,
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_ROLLBACK,
        operation="rollback",
        service=service,
    )


def build_enterprise_approval_execution_adapters(
    mutation_engine_provider: Callable[[], Any],
    *,
    workspace_authorization_mode_change_service: Callable[..., Any] | None = None,
    workspace_authorization_live_mode_resolver: Callable[..., str] | None = None,
) -> dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]]:
    """Return the production action-keyed registry for approval execution."""

    return {
        ACTION_DATASET_ACL_DISABLE: build_dataset_acl_disable_consumer(mutation_engine_provider),
        ACTION_DATASET_WORKSPACE_TRANSFER: build_dataset_workspace_transfer_consumer(
            mutation_engine_provider
        ),
        ACTION_MEMBER_ROLE_CHANGE: build_member_role_change_consumer(mutation_engine_provider),
        ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH: build_knowledge_base_release_publish_consumer(
            mutation_engine_provider
        ),
        ACTION_KNOWLEDGE_BASE_RELEASE_ROLLBACK: build_knowledge_base_release_rollback_consumer(
            mutation_engine_provider
        ),
        ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER: build_knowledge_base_release_quality_waiver_consumer(
            mutation_engine_provider
        ),
        ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE: (
            build_workspace_authorization_mode_change_consumer(
                mutation_engine_provider,
                mode_change_service=workspace_authorization_mode_change_service,
                live_mode_resolver=workspace_authorization_live_mode_resolver,
            )
        ),
    }


# Descriptive aliases keep the registry easy to discover for tests and future
# enterprise consumers without duplicating the production wiring.
build_approval_execution_adapter_registry = build_enterprise_approval_execution_adapters


__all__ = [
    "ACTION_DATASET_ACL_DISABLE",
    "ACTION_DATASET_WORKSPACE_TRANSFER",
    "ACTION_MEMBER_ROLE_CHANGE",
    "ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH",
    "ACTION_KNOWLEDGE_BASE_RELEASE_ROLLBACK",
    "ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER",
    "ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE",
    "ApprovalConsumerError",
    "RESOURCE_KNOWLEDGE_BASE",
    "RESOURCE_TENANT_MEMBER",
    "RESOURCE_TENANT_WORKSPACE",
    "build_approval_execution_adapter_registry",
    "build_dataset_acl_disable_consumer",
    "build_dataset_workspace_transfer_consumer",
    "build_enterprise_approval_execution_adapters",
    "build_member_role_change_consumer",
    "build_knowledge_base_release_publish_consumer",
    "build_knowledge_base_release_rollback_consumer",
    "build_knowledge_base_release_quality_waiver_consumer",
    "build_workspace_authorization_mode_change_consumer",
]
