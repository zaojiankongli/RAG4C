"""Workspace authorization rollout and additive Dataset permission contribution."""

from __future__ import annotations
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import uuid
from typing import Any
from sqlalchemy import MetaData, Table, and_, func, select, text
from sqlalchemy.orm import Session
from core.catalog_schema import inspect_workspace_authorization_capability
from core.enterprise_approval_control import tenant_authority_transaction
from core.enterprise_tenant_idempotency import (
    complete_tenant_mutation,
    engine_serialization_lock,
    idempotency_key_lock,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from core.knowledge_governance import sanitize_audit_snapshot
from core.knowledge_permissions import (
    KNOWLEDGE_AUDIT,
    KNOWLEDGE_DELETE,
    KNOWLEDGE_MANAGE,
    KNOWLEDGE_READ,
    KNOWLEDGE_WRITE,
)
from models.orm import (
    Dataset,
    Tenant,
    TenantAuditEvent,
    TenantMember,
    TenantWorkspace,
    TenantWorkspaceAuthorizationPolicy,
    TenantWorkspaceDataset,
    TenantWorkspaceMember,
)

REVISION = "0027_enterprise_workspace_authorization"
PERMISSION_MODEL_VERSION = 1
ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE = "workspace_authorization_mode_change"
RESOURCE_TENANT_WORKSPACE = "tenant_workspace"
POLICY_TABLE = "tenant_workspace_authorization_policies"
_MODES = frozenset({"disabled", "shadow", "enforced"})
_ROLE_MODEL = {
    "owner": (
        "manager",
        frozenset(
            {KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_DELETE, KNOWLEDGE_MANAGE, KNOWLEDGE_AUDIT}
        ),
    ),
    "admin": (
        "manager",
        frozenset(
            {KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_DELETE, KNOWLEDGE_MANAGE, KNOWLEDGE_AUDIT}
        ),
    ),
    "editor": ("editor", frozenset({KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_DELETE})),
    "viewer": ("viewer", frozenset({KNOWLEDGE_READ})),
}
_ROLE_RANK = {None: 0, "viewer": 1, "editor": 2, "manager": 3}


class WorkspaceAuthorizationError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class WorkspaceAuthorizationUnavailable(WorkspaceAuthorizationError):
    def __init__(self, message="Workspace 授权服务暂不可用"):
        super().__init__("workspace_authorization_unavailable", message, 503)


class WorkspaceAuthorizationForbidden(WorkspaceAuthorizationError):
    def __init__(self, message="当前身份无权管理 Workspace 授权"):
        super().__init__("workspace_authorization_forbidden", message, 403)


class WorkspaceAuthorizationNotFound(WorkspaceAuthorizationError):
    def __init__(self, message="Workspace 授权策略不存在"):
        super().__init__("workspace_authorization_not_found", message, 404)


class WorkspaceAuthorizationValidation(WorkspaceAuthorizationError):
    def __init__(self, message="Workspace 授权请求无效"):
        super().__init__("workspace_authorization_request_invalid", message, 422)


class WorkspaceAuthorizationConflict(WorkspaceAuthorizationError):
    pass


class WorkspaceAuthorizationApprovalRequired(WorkspaceAuthorizationConflict):
    def __init__(self, policy: Mapping[str, Any]):
        self.policy = dict(policy)
        super().__init__(
            "workspace_authorization_approval_required", "Workspace 授权模式变更需要企业审批", 409
        )


@dataclass(frozen=True)
class WorkspaceAuthorizationDecision:
    state: str
    mode: str | None
    permission_model_version: int | None
    workspace_roles: tuple[dict[str, Any], ...]
    contributing_workspaces: tuple[dict[str, Any], ...]
    candidate_permissions: frozenset[str]
    would_grant_permissions: frozenset[str]
    enforced_permissions: frozenset[str]
    granted_permissions: frozenset[str]
    highest_dataset_role: str | None
    policy_revisions: tuple[int, ...]
    warnings: tuple[str, ...] = ()

    def as_payload(self):
        return {
            "state": self.state,
            "mode": self.mode,
            "permission_model_version": self.permission_model_version,
            "workspace_ids": sorted({str(x["workspace_id"]) for x in self.workspace_roles}),
            "workspace_roles": [dict(x) for x in self.workspace_roles],
            "contributing_workspaces": [dict(x) for x in self.contributing_workspaces],
            "candidate_permissions": sorted(self.candidate_permissions),
            "would_grant_permissions": sorted(self.would_grant_permissions),
            "granted_permissions": sorted(self.granted_permissions),
            "policy_revisions": list(self.policy_revisions),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ServiceResult:
    body: dict[str, Any]
    status: int = 200


def workspace_permission_model():
    return {r: {"dataset_role": d, "permissions": sorted(p)} for r, (d, p) in _ROLE_MODEL.items()}


def permission_matrix_fingerprint():
    raw = json.dumps(
        {"permission_model_version": 1, "roles": workspace_permission_model()},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(b"rag4c:workspace-permission-model:v1\0" + raw).hexdigest()


def _clean(v, field, maxlen, allow_empty=False):
    if not isinstance(v, str):
        raise WorkspaceAuthorizationValidation(f"{field} 必须是字符串")
    v = v.strip()
    if (not v and not allow_empty) or len(v) > maxlen:
        raise WorkspaceAuthorizationValidation(f"{field} 无效")
    return v


def _positive(v, field):
    if type(v) is not int or v < 1:
        raise WorkspaceAuthorizationValidation(f"{field} 必须是正整数")
    return v


def _now(v=None):
    return (v or datetime.utcnow()).replace(tzinfo=None)


def _schema_state(bind):
    state, _issues = inspect_workspace_authorization_capability(bind)
    if state == "not_available":
        return state
    if state != "ready":
        raise WorkspaceAuthorizationUnavailable("Workspace 授权 Schema 不可安全使用")
    return state


def _validated_evidence_revision(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise WorkspaceAuthorizationUnavailable(f"{label} revision 无效")
    return value


def _validated_policy_values(
    *,
    mode: Any,
    permission_model_version: Any,
    revision: Any,
    enforced_at: Any,
    enforced_by: Any,
    disabled_at: Any,
    disabled_by: Any,
) -> tuple[str, int, int]:
    if type(mode) is not str:
        raise WorkspaceAuthorizationUnavailable("Workspace 授权策略 mode 无效")
    normalized_mode = mode.strip().casefold()
    if normalized_mode not in _MODES:
        raise WorkspaceAuthorizationUnavailable("Workspace 授权策略 mode 无效")
    if (
        type(permission_model_version) is not int
        or permission_model_version != PERMISSION_MODEL_VERSION
    ):
        raise WorkspaceAuthorizationUnavailable("Workspace 授权策略版本无效")
    if type(revision) is not int or revision < 1:
        raise WorkspaceAuthorizationUnavailable("Workspace 授权策略 revision 无效")
    if normalized_mode == "shadow":
        valid = (
            enforced_at is None
            and enforced_by is None
            and disabled_at is None
            and disabled_by is None
        )
    elif normalized_mode == "enforced":
        valid = (
            enforced_at is not None
            and isinstance(enforced_by, str)
            and bool(enforced_by.strip())
            and disabled_at is None
            and disabled_by is None
        )
    else:
        valid = (
            disabled_at is not None
            and isinstance(disabled_by, str)
            and bool(disabled_by.strip())
            and enforced_at is None
            and enforced_by is None
        )
    if not valid:
        raise WorkspaceAuthorizationUnavailable("Workspace 授权策略 evidence 无效")
    return normalized_mode, permission_model_version, revision


def _validate_approval_execution_fact(
    session: Session,
    fact: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    expected_workspace_revision: int,
    expected_policy_revision: int,
    expected_from_mode: str,
    target_mode: str,
    reason: str,
    permission_matrix_fingerprint: str,
) -> None:
    from core.enterprise_approval_control import ApprovalExecutionFact, _ensure_0025, _execution_id

    if not isinstance(fact, ApprovalExecutionFact):
        raise WorkspaceAuthorizationValidation("approval_execution_fact 必须是内部审批执行事实")
    scalar_fields = (
        (fact.tenant_id, tenant_id),
        (fact.action_type, ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE),
        (fact.resource_type, RESOURCE_TENANT_WORKSPACE),
        (fact.resource_id, workspace_id),
        (fact.workspace_revision, expected_workspace_revision),
        (fact.policy_revision, expected_policy_revision),
        (fact.from_mode, expected_from_mode),
        (fact.target_mode, target_mode),
        (fact.permission_model_version, PERMISSION_MODEL_VERSION),
        (fact.reason, reason),
        (fact.permission_matrix_fingerprint, permission_matrix_fingerprint),
    )
    if any(actual != expected for actual, expected in scalar_fields):
        raise WorkspaceAuthorizationConflict(
            "workspace_authorization_approval_fact_mismatch",
            "审批执行事实与 Workspace 授权变更不匹配",
            409,
        )
    if (
        type(fact.request_revision) is not int
        or fact.request_revision < 1
        or type(fact.execution_revision) is not int
        or fact.execution_revision != fact.request_revision + 1
        or not isinstance(fact.approval_request_id, str)
        or not fact.approval_request_id.strip()
        or not isinstance(fact.execution_id, str)
        or fact.execution_id != _execution_id(tenant_id, fact.approval_request_id)
        or not isinstance(fact.snapshot_hash, str)
        or len(fact.snapshot_hash) != 64
    ):
        raise WorkspaceAuthorizationValidation("approval_execution_fact revisions or identity 无效")

    connection = session.connection()
    _ensure_0025(connection)
    request_table = Table("tenant_approval_requests", MetaData(), autoload_with=connection)
    row = (
        session.execute(
            select(request_table)
            .where(
                request_table.c.tenant_id == tenant_id,
                request_table.c.id == fact.approval_request_id,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise WorkspaceAuthorizationConflict(
            "workspace_authorization_approval_fact_missing",
            "审批执行事实对应的 request 不存在",
            409,
        )
    if (
        str(row["status"]) != "executing"
        or type(row["revision"]) is not int
        or row["revision"] != fact.execution_revision
        or str(row["action_type"]) != ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE
        or str(row["resource_type"]) != RESOURCE_TENANT_WORKSPACE
        or str(row["resource_id"]) != workspace_id
        or str(row["payload_hash"]) != fact.snapshot_hash
        or row["execution_ticket_hash"] is None
        or row["ticket_consumed_at"] is None
    ):
        raise WorkspaceAuthorizationConflict(
            "workspace_authorization_approval_fact_stale",
            "审批执行事实已失效或 scope 不匹配",
            409,
        )
    if str(row["reason"]) != reason:
        raise WorkspaceAuthorizationConflict(
            "workspace_authorization_approval_fact_mismatch",
            "审批 request reason 不匹配",
            409,
        )
    try:
        raw_snapshot = row["snapshot_json"]
        snapshot = json.loads(raw_snapshot) if isinstance(raw_snapshot, str) else raw_snapshot
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkspaceAuthorizationConflict(
            "workspace_authorization_approval_fact_invalid",
            "审批 request snapshot 无效",
            409,
        ) from exc
    if not isinstance(snapshot, Mapping):
        raise WorkspaceAuthorizationConflict(
            "workspace_authorization_approval_fact_invalid",
            "审批 request snapshot 无效",
            409,
        )
    expected_snapshot = {
        "workspace_id": workspace_id,
        "workspace_revision": expected_workspace_revision,
        "policy_revision": expected_policy_revision,
        "from_mode": expected_from_mode,
        "target_mode": target_mode,
        "permission_model_version": PERMISSION_MODEL_VERSION,
        "permission_matrix_fingerprint": permission_matrix_fingerprint,
        "reason": reason,
    }
    for key, value in expected_snapshot.items():
        actual = snapshot.get(key)
        if key == "permission_matrix_fingerprint" and isinstance(actual, str):
            actual = actual.strip().casefold()
        if actual != value:
            raise WorkspaceAuthorizationConflict(
                "workspace_authorization_approval_fact_mismatch",
                "审批 request snapshot 与当前 Workspace 状态不匹配",
                409,
            )


def _state_decision(state: str, warning: str) -> WorkspaceAuthorizationDecision:
    return WorkspaceAuthorizationDecision(
        state,
        None,
        None,
        (),
        (),
        frozenset(),
        frozenset(),
        frozenset(),
        frozenset(),
        None,
        (),
        (warning,),
    )


def _not_available():
    return _state_decision("workspace_authorization_not_available", "Workspace 授权迁移尚未完成")


def workspace_authorization_unavailable_payload(message: str) -> dict[str, Any]:
    return _state_decision("workspace_authorization_unavailable", message).as_payload()


def evaluate_workspace_authorization(
    engine: Any,
    *,
    tenant_id: str,
    account_id: str,
    dataset_id: str,
    existing_permissions: frozenset[str] = frozenset(),
    session: Session | None = None,
):
    tenant = _clean(tenant_id, "tenant_id", 64)
    account = _clean(account_id, "account_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    bind = session.connection() if session is not None else engine
    if _schema_state(bind) == "not_available":
        return _not_available()
    manager = nullcontext(session) if session is not None else Session(engine)
    try:
        with manager as s:
            rows = list(
                s.execute(
                    select(
                        TenantWorkspace.id.label("workspace_id"),
                        TenantWorkspace.name.label("workspace_name"),
                        TenantWorkspace.revision.label("workspace_revision"),
                        TenantWorkspaceDataset.binding_kind,
                        TenantWorkspaceDataset.active_primary_slot,
                        TenantWorkspaceDataset.revision.label("binding_revision"),
                        TenantWorkspaceAuthorizationPolicy.mode.label("policy_mode"),
                        TenantWorkspaceAuthorizationPolicy.permission_model_version,
                        TenantWorkspaceAuthorizationPolicy.revision.label("policy_revision"),
                        TenantWorkspaceAuthorizationPolicy.enforced_at,
                        TenantWorkspaceAuthorizationPolicy.enforced_by,
                        TenantWorkspaceAuthorizationPolicy.disabled_at,
                        TenantWorkspaceAuthorizationPolicy.disabled_by,
                    )
                    .select_from(TenantWorkspace)
                    .join(Tenant, Tenant.id == TenantWorkspace.tenant_id)
                    .join(
                        TenantWorkspaceDataset,
                        and_(
                            TenantWorkspaceDataset.tenant_id == TenantWorkspace.tenant_id,
                            TenantWorkspaceDataset.workspace_id == TenantWorkspace.id,
                        ),
                    )
                    .join(
                        Dataset,
                        and_(
                            Dataset.tenant_id == TenantWorkspaceDataset.tenant_id,
                            Dataset.id == TenantWorkspaceDataset.dataset_id,
                        ),
                    )
                    .outerjoin(
                        TenantWorkspaceAuthorizationPolicy,
                        and_(
                            TenantWorkspaceAuthorizationPolicy.tenant_id
                            == TenantWorkspace.tenant_id,
                            TenantWorkspaceAuthorizationPolicy.workspace_id == TenantWorkspace.id,
                        ),
                    )
                    .where(
                        TenantWorkspace.tenant_id == tenant,
                        Tenant.status == "active",
                        TenantWorkspace.status == "active",
                        TenantWorkspaceDataset.dataset_id == dataset,
                        TenantWorkspaceDataset.status == "active",
                    )
                    .order_by(TenantWorkspace.id)
                ).mappings()
            )
            if any(r["policy_mode"] is None for r in rows):
                raise WorkspaceAuthorizationUnavailable("Active Workspace binding 缺少授权策略")
            ids = [str(r["workspace_id"]) for r in rows]
            memberships = {
                str(workspace_id): (str(role), member_revision)
                for workspace_id, role, member_revision in s.execute(
                    select(
                        TenantWorkspaceMember.workspace_id,
                        TenantWorkspaceMember.role,
                        TenantWorkspaceMember.revision,
                    )
                    .select_from(TenantWorkspaceMember)
                    .join(
                        TenantMember,
                        and_(
                            TenantMember.tenant_id == TenantWorkspaceMember.tenant_id,
                            TenantMember.account_id == TenantWorkspaceMember.account_id,
                        ),
                    )
                    .where(
                        TenantWorkspaceMember.tenant_id == tenant,
                        TenantWorkspaceMember.account_id == account,
                        TenantWorkspaceMember.workspace_id.in_(ids or ["__none__"]),
                        TenantWorkspaceMember.status == "active",
                        TenantMember.status == "active",
                    )
                )
            }
            evidence = []
            contributions = []
            candidate = set()
            enforced = set()
            modes = set()
            revisions = set()
            highest = None
            for row in rows:
                mode, version, revision = _validated_policy_values(
                    mode=row["policy_mode"],
                    permission_model_version=row["permission_model_version"],
                    revision=row["policy_revision"],
                    enforced_at=row["enforced_at"],
                    enforced_by=row["enforced_by"],
                    disabled_at=row["disabled_at"],
                    disabled_by=row["disabled_by"],
                )
                _validated_evidence_revision(row["workspace_revision"], "Workspace")
                _validated_evidence_revision(row["binding_revision"], "Workspace Dataset binding")
                binding_kind = row["binding_kind"]
                if not isinstance(binding_kind, str) or binding_kind.strip().casefold() not in {
                    "primary",
                    "shared",
                }:
                    raise WorkspaceAuthorizationUnavailable("Workspace Dataset binding 无效")
                binding_kind = binding_kind.strip().casefold()
                active_primary_slot = row["active_primary_slot"]
                if (binding_kind == "primary" and active_primary_slot != "primary") or (
                    binding_kind == "shared" and active_primary_slot is not None
                ):
                    raise WorkspaceAuthorizationUnavailable(
                        "Workspace Dataset binding evidence 无效"
                    )
                modes.add(mode)
                revisions.add(revision)
                membership = memberships.get(str(row["workspace_id"]))
                if not membership:
                    continue
                role, member_revision = membership
                _validated_evidence_revision(member_revision, "Workspace member")
                role = role.casefold()
                if role not in _ROLE_MODEL:
                    raise WorkspaceAuthorizationUnavailable("Workspace 成员角色无效")
                dataset_role, perms = _ROLE_MODEL[role]
                item = {
                    "workspace_id": str(row["workspace_id"]),
                    "workspace_name": str(row["workspace_name"]),
                    "role": role,
                    "binding_kind": binding_kind,
                    "policy_mode": mode,
                    "policy_revision": revision,
                }
                evidence.append(item)
                if mode in {"shadow", "enforced"}:
                    candidate.update(perms)
                if mode == "enforced":
                    enforced.update(perms)
                    contributions.append({**item, "permissions": sorted(perms)})
                    if _ROLE_RANK[dataset_role] > _ROLE_RANK[highest]:
                        highest = dataset_role
            state = (
                "workspace_authorization_enforced"
                if "enforced" in modes
                else "workspace_authorization_shadow"
                if "shadow" in modes
                else "workspace_authorization_disabled"
            )
            mode = (
                "enforced" if "enforced" in modes else "shadow" if "shadow" in modes else "disabled"
            )
            return WorkspaceAuthorizationDecision(
                state,
                mode,
                PERMISSION_MODEL_VERSION,
                tuple(evidence),
                tuple(contributions),
                frozenset(candidate),
                frozenset(candidate - set(existing_permissions)),
                frozenset(enforced),
                frozenset(enforced - set(existing_permissions)),
                highest,
                tuple(sorted(revisions)),
                ("Shadow 模式不会改变 effective_permissions",)
                if "shadow" in modes and not enforced
                else (),
            )
    except WorkspaceAuthorizationError:
        raise
    except Exception as exc:
        raise WorkspaceAuthorizationUnavailable("Workspace 授权查询失败") from exc


def authorization_policy_id(tenant_id: str, workspace_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"rag4c:workspace-authorization-policy:v1\x00")
    for value in (tenant_id, workspace_id):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return "workspace-authorization-" + digest.hexdigest()[:40]


def create_shadow_policy_in_session(
    session: Session, *, tenant_id: str, workspace_id: str, actor_id: str, now: datetime
) -> TenantWorkspaceAuthorizationPolicy:
    existing = session.execute(
        select(TenantWorkspaceAuthorizationPolicy).where(
            TenantWorkspaceAuthorizationPolicy.tenant_id == tenant_id,
            TenantWorkspaceAuthorizationPolicy.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    policy = TenantWorkspaceAuthorizationPolicy(
        id=authorization_policy_id(tenant_id, workspace_id),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        mode="shadow",
        permission_model_version=PERMISSION_MODEL_VERSION,
        revision=1,
        created_at=now,
        created_by=actor_id,
        updated_at=now,
        updated_by=actor_id,
    )
    session.add(policy)
    session.flush()
    return policy


def _policy_payload(p):
    def iso(v):
        return v.isoformat(timespec="microseconds") + "Z" if isinstance(v, datetime) else None

    return {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "workspace_id": p.workspace_id,
        "mode": p.mode,
        "permission_model_version": p.permission_model_version,
        "revision": p.revision,
        "created_at": iso(p.created_at),
        "created_by": p.created_by,
        "updated_at": iso(p.updated_at),
        "updated_by": p.updated_by,
        "enforced_at": iso(p.enforced_at),
        "enforced_by": p.enforced_by,
        "disabled_at": iso(p.disabled_at),
        "disabled_by": p.disabled_by,
    }


def _actor(s, tenant, actor):
    row = s.execute(
        select(TenantMember)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .where(
            TenantMember.tenant_id == tenant,
            TenantMember.account_id == actor,
            TenantMember.status == "active",
            Tenant.status == "active",
        )
    ).scalar_one_or_none()
    if row is None:
        raise WorkspaceAuthorizationForbidden("当前身份不是 active TenantMember")
    return row


def _workspace(s, tenant, wid, lock=False):
    q = select(TenantWorkspace).where(
        TenantWorkspace.tenant_id == tenant, TenantWorkspace.id == wid
    )
    if lock:
        q = q.with_for_update()
    row = s.execute(q).scalar_one_or_none()
    if row is None:
        raise WorkspaceAuthorizationNotFound("Workspace 不存在")
    return row


def _lock_tenant(session: Session, tenant_id: str) -> None:
    row = session.execute(
        select(Tenant.id, Tenant.status).where(Tenant.id == tenant_id).with_for_update()
    ).one_or_none()
    if row is None:
        raise WorkspaceAuthorizationNotFound("租户不存在")
    if str(row.status).casefold() != "active":
        raise WorkspaceAuthorizationForbidden("租户当前不可执行 Workspace 授权操作")


def _audit(s, tenant, actor, resource, before, after, request_id, request_ip, now):
    account = actor.account
    event = TenantAuditEvent(
        id=f"tenant-audit-{uuid.uuid4().hex}",
        tenant_id=tenant,
        actor_id=actor.account_id,
        actor_name_snapshot=str(getattr(account, "name", actor.account_id))[:128],
        actor_email_snapshot=str(getattr(account, "email", ""))[:256],
        action="workspace.authorization.mode.changed",
        resource_type="tenant_workspace_authorization_policy",
        resource_id=resource,
        target_account_id=None,
        before_snapshot=sanitize_audit_snapshot(dict(before)),
        after_snapshot=sanitize_audit_snapshot(dict(after)),
        request_id=request_id[:128],
        request_ip=request_ip[:64],
        occurred_at=now,
    )
    s.add(event)
    s.flush()
    return event


def get_workspace_authorization_policy(
    engine: Any, *, tenant_id: str, actor_id: str, workspace_id: str
):
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor_id = _clean(actor_id, "actor_id", 64)
    wid = _clean(workspace_id, "workspace_id", 128)
    with Session(engine) as s:
        if _schema_state(s.connection()) != "ready":
            raise WorkspaceAuthorizationUnavailable("0027 Workspace 授权迁移尚未完成")
        _actor(s, tenant, actor_id)
        _workspace(s, tenant, wid)
        policy = s.execute(
            select(TenantWorkspaceAuthorizationPolicy).where(
                TenantWorkspaceAuthorizationPolicy.tenant_id == tenant,
                TenantWorkspaceAuthorizationPolicy.workspace_id == wid,
            )
        ).scalar_one_or_none()
        if policy is None:
            raise WorkspaceAuthorizationUnavailable("Workspace 缺少授权策略")
        members = s.scalar(
            select(func.count())
            .select_from(TenantWorkspaceMember)
            .where(
                TenantWorkspaceMember.tenant_id == tenant,
                TenantWorkspaceMember.workspace_id == wid,
                TenantWorkspaceMember.status == "active",
            )
        )
        bindings = s.scalar(
            select(func.count())
            .select_from(TenantWorkspaceDataset)
            .where(
                TenantWorkspaceDataset.tenant_id == tenant,
                TenantWorkspaceDataset.workspace_id == wid,
                TenantWorkspaceDataset.status == "active",
            )
        )
        revision = s.scalar(text("SELECT version_num FROM alembic_version"))
        from core.enterprise_approval_control import resolve_active_approval_policy_in_session

        approval = resolve_active_approval_policy_in_session(
            s,
            tenant_id=tenant,
            action_type=ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE,
            resource_type=RESOURCE_TENANT_WORKSPACE,
            resource_id=wid,
        )
        evidence = {
            "active_member_count": int(members or 0),
            "active_dataset_binding_count": int(bindings or 0),
            "catalog_revision": str(revision) if revision else None,
            "permission_matrix_fingerprint": permission_matrix_fingerprint(),
            "matching_approval_policy": (
                {
                    "state": str(approval.get("status", "active")),
                    "id": str(approval["id"]),
                    "name": str(approval["name"]),
                    "required_approvals": int(approval["required_approvals"]),
                    "execution_adapter_status": "connected",
                }
                if approval
                else None
            ),
            "latest_audit_event": None,
        }
        return ServiceResult({"policy": _policy_payload(policy), "evidence": evidence})


def get_workspace_authorization_impact(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    dataset_id: str,
) -> ServiceResult:
    from core.enterprise_access_control import evaluate_dataset_permissions

    tenant = _clean(tenant_id, "tenant_id", 64)
    actor_id = _clean(actor_id, "actor_id", 64)
    workspace_id = _clean(workspace_id, "workspace_id", 128)
    dataset_id = _clean(dataset_id, "dataset_id", 64)
    with Session(engine) as session:
        if _schema_state(session.connection()) != "ready":
            raise WorkspaceAuthorizationUnavailable("0027 Workspace 授权迁移尚未完成")
        actor = _actor(session, tenant, actor_id)
        _workspace(session, tenant, workspace_id)
        binding = session.execute(
            select(TenantWorkspaceDataset.id).where(
                TenantWorkspaceDataset.tenant_id == tenant,
                TenantWorkspaceDataset.workspace_id == workspace_id,
                TenantWorkspaceDataset.dataset_id == dataset_id,
                TenantWorkspaceDataset.status == "active",
            )
        ).scalar_one_or_none()
        if binding is None:
            raise WorkspaceAuthorizationNotFound("Dataset 未绑定到该 Workspace")
        decision = evaluate_dataset_permissions(
            engine,
            tenant,
            actor_id,
            str(actor.role),
            dataset_id,
            session=session,
        )
        workspace = dict(decision.workspace_authorization or {})
        matched = [
            {
                "id": str(item.get("id", "")),
                "subject_type": str(item.get("subject_type", "")),
                "subject_id": str(item.get("subject_id", "")),
                "subject_name": str(item.get("subject_id", "")),
                "role": str(item.get("role", "")),
            }
            for item in decision.matched_grants
        ]
        impact = {
            "dataset_id": dataset_id,
            "state": workspace.get("state", "workspace_authorization_not_available"),
            "tenant_role": str(actor.role),
            "dataset_acl_role": decision.dataset_role,
            "matched_grants": matched,
            "workspace_roles": workspace.get("workspace_roles", []),
            "contributing_workspaces": workspace.get("contributing_workspaces", []),
            "current_effective_permissions": sorted(decision.effective_permissions),
            "candidate_permissions": workspace.get("candidate_permissions", []),
            "would_grant_permissions": workspace.get("would_grant_permissions", []),
            "granted_permissions": workspace.get("granted_permissions", []),
            "warnings": list(decision.warnings),
        }
        return ServiceResult({"impact": impact})


def change_workspace_authorization_mode(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    expected_policy_revision: int,
    target_mode: str,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
    expected_workspace_revision: int | None = None,
    actor_role: str | None = None,
    expected_from_mode: str | None = None,
    expected_permission_model_version: int = PERMISSION_MODEL_VERSION,
    permission_matrix_fingerprint: str | None = None,
    approval_execution_fact: Any | None = None,
    approval_execution_id: str | None = None,
    now: datetime | None = None,
):
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor_id = _clean(actor_id, "actor_id", 64)
    wid = _clean(workspace_id, "workspace_id", 128)
    target = _clean(target_mode, "target_mode", 16).casefold()
    reason = _clean(reason, "reason", 512)
    key = _clean(idempotency_key, "idempotency_key", 128)
    req = _clean(request_id, "request_id", 128)
    ip = _clean(request_ip, "request_ip", 64, True)
    if target not in _MODES:
        raise WorkspaceAuthorizationValidation("target_mode 无效")
    prev = _positive(expected_policy_revision, "expected_policy_revision")
    wrev = (
        _positive(expected_workspace_revision, "expected_workspace_revision")
        if expected_workspace_revision is not None
        else None
    )
    expected_from = (
        _clean(expected_from_mode, "expected_from_mode", 16).casefold()
        if expected_from_mode is not None
        else None
    )
    if expected_from is not None and expected_from not in _MODES:
        raise WorkspaceAuthorizationValidation("expected_from_mode 无效")
    if approval_execution_id is not None:
        raise WorkspaceAuthorizationValidation(
            "approval_execution_id 已废弃；必须提供内部 approval_execution_fact"
        )
    approval_fact_id = None
    if approval_execution_fact is not None:
        from core.enterprise_approval_control import ApprovalExecutionFact

        if not isinstance(approval_execution_fact, ApprovalExecutionFact):
            raise WorkspaceAuthorizationValidation("approval_execution_fact 无效")
        approval_fact_id = approval_execution_fact.execution_id
    fingerprint = globals()["permission_matrix_fingerprint"]()
    if (
        type(expected_permission_model_version) is not int
        or expected_permission_model_version != PERMISSION_MODEL_VERSION
        or (
            permission_matrix_fingerprint is not None
            and permission_matrix_fingerprint.strip().casefold() != fingerprint
        )
    ):
        raise WorkspaceAuthorizationConflict(
            "workspace_authorization_model_conflict", "权限模型版本或指纹已变化", 409
        )
    current = _now(now)
    request_hash = tenant_request_hash(
        operation="workspace.authorization.mode.change",
        path_identity={"tenant_id": tenant, "workspace_id": wid},
        body={
            "policy_revision": prev,
            "workspace_revision": wrev,
            "expected_from_mode": expected_from,
            "target_mode": target,
            "permission_model_version": PERMISSION_MODEL_VERSION,
            "permission_matrix_fingerprint": fingerprint,
            "reason": reason,
            "approval_execution_id": approval_fact_id,
        },
    )
    digest = tenant_idempotency_key_digest(tenant, actor_id, key)
    with (
        idempotency_key_lock(tenant, actor_id, digest),
        engine_serialization_lock(engine),
        Session(engine, expire_on_commit=False) as session,
    ):
        with tenant_authority_transaction(session):
            if _schema_state(session.connection()) != "ready":
                raise WorkspaceAuthorizationUnavailable("0027 Workspace 授权迁移尚未完成")
            actor = _actor(session, tenant, actor_id)
            if actor.role not in {"owner", "admin"}:
                raise WorkspaceAuthorizationForbidden()
            _lock_tenant(session, tenant)
            workspace = _workspace(session, tenant, wid, True)
            if wrev is not None and workspace.revision != wrev:
                raise WorkspaceAuthorizationConflict(
                    "workspace_authorization_workspace_revision_conflict",
                    "Workspace revision 已变化",
                    409,
                )
            if target in {"shadow", "enforced"} and workspace.status != "active":
                raise WorkspaceAuthorizationConflict(
                    "workspace_authorization_workspace_archived",
                    "Archived Workspace 不能启用授权",
                    409,
                )
            policy = session.execute(
                select(TenantWorkspaceAuthorizationPolicy)
                .where(
                    TenantWorkspaceAuthorizationPolicy.tenant_id == tenant,
                    TenantWorkspaceAuthorizationPolicy.workspace_id == wid,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if policy is None:
                raise WorkspaceAuthorizationUnavailable("Workspace 缺少授权策略")
            current_mode, _version, policy_revision = _validated_policy_values(
                mode=policy.mode,
                permission_model_version=policy.permission_model_version,
                revision=policy.revision,
                enforced_at=policy.enforced_at,
                enforced_by=policy.enforced_by,
                disabled_at=policy.disabled_at,
                disabled_by=policy.disabled_by,
            )
            reservation = reserve_tenant_mutation(
                session,
                tenant_id=tenant,
                actor_id=actor_id,
                raw_idempotency_key=key,
                request_hash=request_hash,
                operation="workspace.authorization.mode.change",
                resource_type="tenant_workspace_authorization_policy",
            )
            if reservation.replay is not None:
                return reservation.replay.response
            if policy_revision != prev:
                raise WorkspaceAuthorizationConflict(
                    "workspace_authorization_policy_revision_conflict",
                    "Policy revision 已变化",
                    409,
                )
            if expected_from is not None and expected_from != current_mode:
                raise WorkspaceAuthorizationConflict(
                    "workspace_authorization_from_mode_conflict",
                    "Workspace 当前模式与审批 snapshot 不匹配",
                    409,
                )
            if current_mode == target:
                raise WorkspaceAuthorizationConflict(
                    "workspace_authorization_mode_conflict", "目标模式与当前模式相同", 409
                )
            legal_transitions = {
                ("disabled", "shadow"),
                ("shadow", "enforced"),
                ("enforced", "shadow"),
                ("enforced", "disabled"),
            }
            if (current_mode, target) not in legal_transitions:
                raise WorkspaceAuthorizationConflict(
                    "workspace_authorization_invalid_transition",
                    "Workspace 授权模式转换不合法",
                    409,
                )
            from core.enterprise_approval_control import resolve_active_approval_policy_in_session

            approval = resolve_active_approval_policy_in_session(
                session,
                tenant_id=tenant,
                action_type=ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE,
                resource_type=RESOURCE_TENANT_WORKSPACE,
                resource_id=wid,
                lock_for_update=True,
                tenant_already_locked=True,
            )
            approval_required = approval is not None and (
                target in {"enforced", "disabled"}
                or (current_mode == "enforced" and target == "shadow")
            )
            if approval_required:
                if approval_execution_fact is None:
                    raise WorkspaceAuthorizationApprovalRequired(approval)
                _validate_approval_execution_fact(
                    session,
                    approval_execution_fact,
                    tenant_id=tenant,
                    workspace_id=wid,
                    expected_workspace_revision=workspace.revision,
                    expected_policy_revision=policy_revision,
                    expected_from_mode=current_mode,
                    target_mode=target,
                    reason=reason,
                    permission_matrix_fingerprint=fingerprint,
                )
            elif approval_execution_fact is not None:
                raise WorkspaceAuthorizationValidation("当前模式转换不接受审批执行事实")
            before = _policy_payload(policy)
            policy.mode = target
            policy.revision += 1
            policy.updated_at = current
            policy.updated_by = actor_id
            policy.enforced_at = current if target == "enforced" else None
            policy.enforced_by = actor_id if target == "enforced" else None
            policy.disabled_at = current if target == "disabled" else None
            policy.disabled_by = actor_id if target == "disabled" else None
            session.flush()
            after = _policy_payload(policy)
            audit = _audit(
                session,
                tenant,
                actor,
                policy.id,
                before,
                {**after, "reason": reason, "approval_execution_id": approval_fact_id},
                req,
                ip,
                current,
            )
            payload = {
                "authorization_policy": after,
                "workspace": {
                    "id": workspace.id,
                    "tenant_id": workspace.tenant_id,
                    "status": workspace.status,
                    "revision": workspace.revision,
                },
                "audit": {"id": audit.id, "sequence": audit.sequence},
            }
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=payload,
                http_status=200,
                resource_id=policy.id,
            )
            return payload


__all__ = [
    "ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE",
    "PERMISSION_MODEL_VERSION",
    "RESOURCE_TENANT_WORKSPACE",
    "ServiceResult",
    "WorkspaceAuthorizationApprovalRequired",
    "WorkspaceAuthorizationConflict",
    "WorkspaceAuthorizationDecision",
    "WorkspaceAuthorizationError",
    "WorkspaceAuthorizationForbidden",
    "WorkspaceAuthorizationNotFound",
    "WorkspaceAuthorizationUnavailable",
    "WorkspaceAuthorizationValidation",
    "authorization_policy_id",
    "change_workspace_authorization_mode",
    "create_shadow_policy_in_session",
    "evaluate_workspace_authorization",
    "get_workspace_authorization_impact",
    "get_workspace_authorization_policy",
    "permission_matrix_fingerprint",
    "workspace_permission_model",
]
