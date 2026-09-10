"""SQLAlchemy 关系模型：多租户数据层（关系 / 配额 / 状态 / 元数据 schema）。

分层（关系与状态在 SQLite，向量与过滤在 Milvus）：

    Account -> Tenant -> App -> Workflow
                   └-> Dataset -> Document -> DocumentSegment
                                                  └-> Chunk（Milvus，parent_chunk_id 链出 ChildChunk）

- 文档状态机状态（waiting / parsing / splitting / indexing / completed / error）
  持久化于 Document / DocumentSegment（文档级 + 分段级双粒度进度）。
- MetadataField 为「元数据 schema 注册表」：声明某知识库可过滤的用户字段
  （键名 + 值类型 + 来源 manual/auto），供 manual 过滤与 LLM 自动过滤共用，
  表达式校验器（core.metadata）以此为白名单。

本模块仅依赖 sqlalchemy（可选：未安装时导入报错，其余系统不受影响）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    JSON,
    DateTime,
    Float,
    ForeignKey,
    FetchedValue,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    column,
    literal,
    or_,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """ORM 基类。"""


# ---------------------------------------------------------------------------
# 文档状态机（模块常量 + 合法迁移）
# ---------------------------------------------------------------------------

DOC_STATUSES: tuple[str, ...] = (
    "waiting",
    "parsing",
    "splitting",
    "indexing",
    "completed",
    "error",
    "deleting",
)

# 合法状态迁移（retry 语义：error 可回到任意前序阶段；幂等重跑允许同级）
_DOC_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "waiting": ("parsing", "error", "deleting"),
    "parsing": ("splitting", "error", "parsing", "deleting"),
    "splitting": ("indexing", "error", "splitting", "deleting"),
    "indexing": ("completed", "error", "indexing", "deleting"),
    "completed": ("completed", "deleting"),
    "error": ("waiting", "parsing", "splitting", "indexing", "deleting"),
    "deleting": ("deleting",),
}

SEGMENT_STATUSES: tuple[str, ...] = (
    "pending",
    "processing",
    "indexed",
    "error",
)


def valid_transition(cur: str, nxt: str) -> bool:
    """校验文档状态迁移合法性（供状态机使用）。"""
    return nxt in _DOC_TRANSITIONS.get(cur, ())


# ---------------------------------------------------------------------------
# 关系模型
# ---------------------------------------------------------------------------


def _pk() -> Mapped[str]:
    return mapped_column(String(64), primary_key=True)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime, default=datetime.utcnow)


def _datetime6():
    return (
        DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _required_context_value(context: Any, key: str) -> str:
    value = context.get_current_parameters().get(key)
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{key} is required")
    return normalized


def _default_organization_code(context: Any) -> str:
    return _required_context_value(context, "id")


def _default_normalized_name(context: Any) -> str:
    return _required_context_value(context, "name").casefold()


def _default_normalized_email(context: Any) -> str:
    return _required_context_value(context, "email").casefold()


def _task_saved_view_active_key_check():
    """Return the canonical active Saved View identity predicate."""
    status = column("status", String(16))
    account_id = column("account_id", String(64))
    normalized_name = column("normalized_name", String(128))
    active_key = column("active_view_key", String(192))
    return or_(
        and_(status == "archived", active_key.is_(None)),
        and_(
            status == "active",
            active_key == account_id + literal(":") + normalized_name,
        ),
    )


def _quality_policy_active_scope_key_check():
    """Return the canonical active policy scope predicate for all SQL dialects."""
    status = column("status", String(16))
    scope_type = column("scope_type", String(16))
    scope_value = column("scope_value", String(128))
    active_scope_key = column("active_scope_key", String(192))
    return or_(
        status != "active",
        and_(scope_type == "global", active_scope_key == "global:*"),
        and_(
            scope_type == "risk_tier",
            active_scope_key == literal("risk_tier:") + scope_value,
        ),
        and_(
            scope_type == "channel",
            active_scope_key == literal("channel:") + scope_value,
        ),
    )


def _quality_operations_alert_active_key_check():
    status = column("status", String(16))
    active_key = column("active_alert_key", String(384))
    expected = (
        column("dataset_id", String(64))
        + literal(":")
        + column("release_id", String(64))
        + literal(":")
        + column("channel_id", String(128))
        + literal(":")
        + column("release_role", String(16))
        + literal(":")
        + column("alert_type", String(40))
    )
    return or_(
        and_(status == "resolved", active_key.is_(None)),
        and_(status != "resolved", active_key == expected),
    )


def _quality_operations_job_active_key_check():
    status = column("status", String(24))
    active_key = column("active_job_key", String(384))
    expected = (
        column("dataset_id", String(64))
        + literal(":")
        + column("release_id", String(64))
        + literal(":")
        + column("channel_id", String(128))
        + literal(":")
        + column("release_role", String(16))
        + literal(":")
        + column("policy_id", String(64))
    )
    return or_(
        and_(status.in_(("completed", "failed", "cancelled")), active_key.is_(None)),
        and_(
            status.in_(("pending", "claimed", "awaiting_evidence", "ready_to_certify")),
            active_key == expected,
        ),
    )


def _notification_subscription_active_key_check():
    """Return the canonical active subscription identity predicate."""
    status = column("status", String(16))
    account_id = column("account_id", String(64))
    category = column("category", String(16))
    active_key = column("active_subscription_key", String(96))
    return or_(
        and_(status == "archived", active_key.is_(None)),
        and_(status == "active", active_key == account_id + literal(":") + category),
    )


def _content_recovery_recycle_active_key_check():
    """Return the canonical active recycle identity predicate."""
    status = column("status", String(24))
    active_key = column("active_recycle_key", String(160))
    expected = column("dataset_id", String(64)) + literal(":") + column("document_id", String(64))
    return or_(
        and_(status == "recycled", active_key.is_not(None), active_key == expected),
        and_(status == "restoring", active_key.is_not(None), active_key == expected),
        and_(status == "purge_requested", active_key.is_not(None), active_key == expected),
        and_(status.in_(("restored", "purged", "failed")), active_key.is_(None)),
    )


def _content_recovery_hold_active_key_check():
    """Return the canonical active legal-hold identity predicate."""
    status = column("status", String(16))
    active_key = column("active_hold_key", String(192))
    expected = (
        column("recycle_entry_id", String(64)) + literal(":") + column("reason_code", String(64))
    )
    return or_(
        and_(status == "active", active_key.is_not(None), active_key == expected),
        and_(status == "released", active_key.is_(None)),
    )


class Account(Base):
    """用户账号。"""

    __tablename__ = "accounts"

    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    created_at: Mapped[datetime] = _now()

    memberships: Mapped[list["TenantMember"]] = relationship(back_populates="account")


class Tenant(Base):
    """租户（工作空间）：隔离与配额边界。"""

    __tablename__ = "tenants"

    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String(128))
    plan: Mapped[str] = mapped_column(String(32), default="free")  # free | pro | enterprise
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | suspended
    quota_documents: Mapped[int] = mapped_column(Integer, default=1000)
    quota_chunks: Mapped[int] = mapped_column(Integer, default=100_000)
    # 当前用量（由入库/删除路径经 bump_counts 维护，用于配额检查）
    doc_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _now()

    members: Mapped[list["TenantMember"]] = relationship(back_populates="tenant")
    audit_events: Mapped[list["TenantAuditEvent"]] = relationship(back_populates="tenant")
    apps: Mapped[list["App"]] = relationship(back_populates="tenant")
    datasets: Mapped[list["Dataset"]] = relationship(back_populates="tenant")
    workspaces: Mapped[list["TenantWorkspace"]] = relationship(back_populates="tenant")
    dataset_workspace_ownerships: Mapped[list["DatasetWorkspaceOwnership"]] = relationship(
        back_populates="tenant",
        foreign_keys=lambda: [DatasetWorkspaceOwnership.tenant_id],
        overlaps="dataset_ownerships,workspace_ownership,tenant,dataset,workspace",
    )
    app_dataset_references: Mapped[list["AppDatasetReference"]] = relationship(
        back_populates="tenant",
        foreign_keys=lambda: [AppDatasetReference.tenant_id],
        overlaps="dataset_references,app_references,tenant,app,dataset",
    )


class TenantMember(Base):
    """账号-租户成员关系（角色与生命周期）。"""

    __tablename__ = "tenant_members"
    __table_args__ = (
        UniqueConstraint("account_id", "tenant_id", name="uq_tenant_members_account_tenant"),
        UniqueConstraint("tenant_id", "account_id", name="uq_tenant_members_tenant_account"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'member')",
            name="ck_tenant_members_role",
        ),
        CheckConstraint(
            "status IN ('active', 'suspended')",
            name="ck_tenant_members_status",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_tenant_members_revision_positive",
        ),
        Index(
            "ix_tenant_members_tenant_status_role_id",
            "tenant_id",
            "status",
            "role",
            "id",
        ),
        Index(
            "ix_tenant_members_tenant_account_status",
            "tenant_id",
            "account_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64), default="system")
    suspended_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    suspended_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)

    account: Mapped["Account"] = relationship(back_populates="memberships")
    tenant: Mapped["Tenant"] = relationship(back_populates="members")


class TenantWorkspace(Base):
    """Explicit tenant workspace authority; it does not grant Dataset permissions by itself."""

    __tablename__ = "tenant_workspaces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_workspaces_scope_id"),
        UniqueConstraint("tenant_id", "code", name="uq_tenant_workspaces_tenant_code"),
        UniqueConstraint(
            "tenant_id",
            "normalized_name",
            name="uq_tenant_workspaces_tenant_normalized_name",
        ),
        UniqueConstraint(
            "tenant_id",
            "active_default_slot",
            name="uq_tenant_workspaces_active_default",
        ),
        CheckConstraint("status IN ('active','archived')", name="ck_tenant_workspaces_status"),
        CheckConstraint(
            "environment IN ('development','testing','production')",
            name="ck_tenant_workspaces_environment",
        ),
        CheckConstraint("revision > 0", name="ck_tenant_workspaces_revision_positive"),
        CheckConstraint(
            "(status = 'active' AND archived_at IS NULL AND archived_by IS NULL) "
            "OR (status = 'archived' AND archived_at IS NOT NULL AND archived_by IS NOT NULL)",
            name="ck_tenant_workspaces_lifecycle_evidence",
        ),
        CheckConstraint(
            "(is_default = false AND active_default_slot IS NULL) OR "
            "(is_default = true AND status = 'active' AND active_default_slot = 'default')",
            name="ck_tenant_workspaces_active_default_slot",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tenant_workspaces_tenant"),
        Index(
            "ix_tenant_workspaces_tenant_status_updated",
            "tenant_id",
            "status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_tenant_workspaces_tenant_default",
            "tenant_id",
            "status",
            "active_default_slot",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    environment: Mapped[str] = mapped_column(String(16), default="production")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active_default_slot: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    archived_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    archived_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)

    tenant: Mapped["Tenant"] = relationship(back_populates="workspaces")
    members: Mapped[list["TenantWorkspaceMember"]] = relationship(
        back_populates="workspace",
        foreign_keys=lambda: [TenantWorkspaceMember.tenant_id, TenantWorkspaceMember.workspace_id],
    )
    dataset_bindings: Mapped[list["TenantWorkspaceDataset"]] = relationship(
        back_populates="workspace",
        foreign_keys=lambda: [
            TenantWorkspaceDataset.tenant_id,
            TenantWorkspaceDataset.workspace_id,
        ],
        overlaps="dataset,workspace_bindings",
    )
    authorization_policy: Mapped[Optional["TenantWorkspaceAuthorizationPolicy"]] = relationship(
        back_populates="workspace",
        uselist=False,
        foreign_keys=lambda: [
            TenantWorkspaceAuthorizationPolicy.tenant_id,
            TenantWorkspaceAuthorizationPolicy.workspace_id,
        ],
    )
    dataset_ownerships: Mapped[list["DatasetWorkspaceOwnership"]] = relationship(
        back_populates="workspace",
        foreign_keys=lambda: [
            DatasetWorkspaceOwnership.tenant_id,
            DatasetWorkspaceOwnership.workspace_id,
        ],
        overlaps="dataset_workspace_ownerships,workspace_ownership,tenant,dataset",
    )


class TenantWorkspaceMember(Base):
    """Tenant-safe workspace membership copied from an existing TenantMember."""

    __tablename__ = "tenant_workspace_members"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "account_id",
            name="uq_tenant_workspace_members_scope_account",
        ),
        CheckConstraint(
            "role IN ('owner','admin','editor','viewer')",
            name="ck_tenant_workspace_members_role",
        ),
        CheckConstraint(
            "status IN ('active','removed')", name="ck_tenant_workspace_members_status"
        ),
        CheckConstraint("revision > 0", name="ck_tenant_workspace_members_revision_positive"),
        CheckConstraint(
            "status <> 'removed' OR (removed_at IS NOT NULL AND removed_by IS NOT NULL)",
            name="ck_tenant_workspace_members_removed_evidence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_workspace_members_scope_workspace",
        ),
        ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_workspace_members_scope_member",
        ),
        Index(
            "ix_tenant_workspace_members_tenant_workspace_status",
            "tenant_id",
            "workspace_id",
            "status",
            "id",
        ),
        Index(
            "ix_tenant_workspace_members_tenant_account_status",
            "tenant_id",
            "account_id",
            "status",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    workspace_id: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    removed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    removed_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)

    workspace: Mapped["TenantWorkspace"] = relationship(
        back_populates="members",
        foreign_keys=lambda: [TenantWorkspaceMember.tenant_id, TenantWorkspaceMember.workspace_id],
    )


class TenantWorkspaceDataset(Base):
    """Tenant-safe Dataset binding; workspace membership is not Dataset authorization."""

    __tablename__ = "tenant_workspace_datasets"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "dataset_id",
            name="uq_tenant_workspace_datasets_scope_binding",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "active_primary_slot",
            name="uq_tenant_workspace_datasets_active_primary",
        ),
        CheckConstraint(
            "binding_kind IN ('primary','shared')",
            name="ck_tenant_workspace_datasets_binding_kind",
        ),
        CheckConstraint(
            "status IN ('active','removed')", name="ck_tenant_workspace_datasets_status"
        ),
        CheckConstraint("revision > 0", name="ck_tenant_workspace_datasets_revision_positive"),
        CheckConstraint(
            "(status = 'active' AND binding_kind = 'primary' "
            "AND active_primary_slot IS NOT NULL AND active_primary_slot = 'primary') "
            "OR ((status <> 'active' OR binding_kind <> 'primary') AND active_primary_slot IS NULL)",
            name="ck_tenant_workspace_datasets_active_primary_slot",
        ),
        CheckConstraint(
            "status <> 'removed' OR (removed_at IS NOT NULL AND removed_by IS NOT NULL)",
            name="ck_tenant_workspace_datasets_removed_evidence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_workspace_datasets_scope_workspace",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_workspace_datasets_scope_dataset",
        ),
        Index(
            "ix_tenant_workspace_datasets_tenant_workspace_status",
            "tenant_id",
            "workspace_id",
            "status",
            "id",
        ),
        Index(
            "ix_tenant_workspace_datasets_tenant_dataset_status",
            "tenant_id",
            "dataset_id",
            "status",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    workspace_id: Mapped[str] = mapped_column(String(128))
    dataset_id: Mapped[str] = mapped_column(String(64))
    binding_kind: Mapped[str] = mapped_column(String(16))
    active_primary_slot: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    removed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    removed_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)

    workspace: Mapped["TenantWorkspace"] = relationship(
        back_populates="dataset_bindings",
        foreign_keys=lambda: [
            TenantWorkspaceDataset.tenant_id,
            TenantWorkspaceDataset.workspace_id,
        ],
        overlaps="dataset,workspace_bindings",
    )
    dataset: Mapped["Dataset"] = relationship(
        back_populates="workspace_bindings",
        foreign_keys=lambda: [TenantWorkspaceDataset.tenant_id, TenantWorkspaceDataset.dataset_id],
        overlaps="dataset_bindings,workspace",
    )


class TenantWorkspaceAuthorizationPolicy(Base):
    """Tenant-safe Workspace authorization rollout authority."""

    __tablename__ = "tenant_workspace_authorization_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_tenant_workspace_authorization_policies_scope_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            name="uq_tenant_workspace_authorization_policies_scope_workspace",
        ),
        CheckConstraint(
            "mode IN ('disabled','shadow','enforced')",
            name="ck_tenant_workspace_auth_policies_mode",
        ),
        CheckConstraint(
            "permission_model_version > 0",
            name="ck_tenant_workspace_auth_policies_model_version_positive",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_tenant_workspace_auth_policies_revision_positive",
        ),
        CheckConstraint(
            "(mode = 'shadow' AND enforced_at IS NULL AND enforced_by IS NULL "
            "AND disabled_at IS NULL AND disabled_by IS NULL) OR "
            "(mode = 'enforced' AND enforced_at IS NOT NULL AND enforced_by IS NOT NULL "
            "AND disabled_at IS NULL AND disabled_by IS NULL) OR "
            "(mode = 'disabled' AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL "
            "AND enforced_at IS NULL AND enforced_by IS NULL)",
            name="ck_tenant_workspace_auth_policies_mode_evidence",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_workspace_authorization_policies_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_workspace_authorization_policies_scope_workspace",
        ),
        Index(
            "ix_tw_auth_policies_tenant_mode_updated",
            "tenant_id",
            "mode",
            "updated_at",
            "id",
        ),
        Index(
            "ix_tw_auth_policies_tenant_workspace_mode",
            "tenant_id",
            "workspace_id",
            "mode",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    workspace_id: Mapped[str] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(16))
    permission_model_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    enforced_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    enforced_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    disabled_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    disabled_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)

    workspace: Mapped["TenantWorkspace"] = relationship(
        back_populates="authorization_policy",
        foreign_keys=lambda: [
            TenantWorkspaceAuthorizationPolicy.tenant_id,
            TenantWorkspaceAuthorizationPolicy.workspace_id,
        ],
    )


class TenantOrganizationUnit(Base):
    """Tenant-scoped organization hierarchy node with optimistic revision."""

    __tablename__ = "tenant_organization_units"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_organization_units_scope_id"),
        UniqueConstraint("tenant_id", "code", name="uq_tenant_organization_units_tenant_code"),
        UniqueConstraint(
            "tenant_id",
            "parent_id",
            "name",
            name="uq_tenant_organization_units_tenant_parent_name",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_organization_units_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            [
                "tenant_organization_units.tenant_id",
                "tenant_organization_units.id",
            ],
            name="fk_tenant_organization_units_scope_parent",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_tenant_organization_units_status",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_tenant_organization_units_revision_positive",
        ),
        Index(
            "ix_tenant_organization_units_tenant_parent_status",
            "tenant_id",
            "parent_id",
            "status",
            "sort_order",
            "id",
        ),
        Index(
            "ix_tenant_organization_units_tenant_status",
            "tenant_id",
            "status",
            "sort_order",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    parent_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(64), default=_default_organization_code)
    status: Mapped[str] = mapped_column(String(16), default="active")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64), default="system")


class TenantOrganizationUnitMember(Base):
    """Tenant-safe assignment of an account to an organization unit."""

    __tablename__ = "tenant_organization_unit_members"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "organization_unit_id",
            "account_id",
            name="uq_tenant_organization_unit_members_unit_account",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "organization_unit_id"],
            [
                "tenant_organization_units.tenant_id",
                "tenant_organization_units.id",
            ],
            name="fk_tenant_organization_unit_members_scope_unit",
        ),
        ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_organization_unit_members_scope_account",
        ),
        CheckConstraint(
            "status IN ('active', 'removed')",
            name="ck_tenant_organization_unit_members_status",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_tenant_organization_unit_members_revision_positive",
        ),
        Index(
            "ix_tenant_organization_unit_members_tenant_unit_status",
            "tenant_id",
            "organization_unit_id",
            "status",
            "id",
        ),
        Index(
            "ix_tenant_organization_unit_members_tenant_account_status",
            "tenant_id",
            "account_id",
            "status",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    organization_unit_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64), default="system")


class TenantGroup(Base):
    """Tenant-scoped governed group identity."""

    __tablename__ = "tenant_groups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_groups_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "normalized_name",
            name="uq_tenant_groups_tenant_normalized_name",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_groups_tenant",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_tenant_groups_status",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_tenant_groups_revision_positive",
        ),
        Index(
            "ix_tenant_groups_tenant_status_name",
            "tenant_id",
            "status",
            "normalized_name",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(256), default=_default_normalized_name)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class TenantGroupMember(Base):
    """Tenant-safe account membership in a governed group."""

    __tablename__ = "tenant_group_members"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "group_id",
            "account_id",
            name="uq_tenant_group_members_group_account",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "group_id"],
            ["tenant_groups.tenant_id", "tenant_groups.id"],
            name="fk_tenant_group_members_scope_group",
        ),
        ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_group_members_scope_account",
        ),
        CheckConstraint(
            "status IN ('active', 'removed')",
            name="ck_tenant_group_members_status",
        ),
        Index(
            "ix_tenant_group_members_tenant_account",
            "tenant_id",
            "account_id",
            "id",
        ),
        Index(
            "ix_tenant_group_members_tenant_group",
            "tenant_id",
            "group_id",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    group_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")


class DatasetAccessGrant(Base):
    """Dataset-scoped role granted to an account, group, or organization unit."""

    __tablename__ = "dataset_access_grants"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "subject_type",
            "subject_id",
            name="uq_dataset_access_grants_dataset_subject",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_access_grants_scope_dataset",
        ),
        CheckConstraint(
            "subject_type IN ('account', 'group', 'organization_unit')",
            name="ck_dataset_access_grants_subject_type",
        ),
        CheckConstraint(
            "role IN ('viewer', 'editor', 'manager')",
            name="ck_dataset_access_grants_role",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_dataset_access_grants_status",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_dataset_access_grants_revision_positive",
        ),
        Index(
            "ix_dataset_access_grants_tenant_dataset_status",
            "tenant_id",
            "dataset_id",
            "status",
            "id",
        ),
        Index(
            "ix_dataset_access_grants_tenant_subject",
            "tenant_id",
            "subject_type",
            "subject_id",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class TenantInvitation(Base):
    """Tenant invitation authority that persists only a token hash."""

    __tablename__ = "tenant_invitations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "pending_email_key",
            name="uq_tenant_invitations_pending_email",
        ),
        UniqueConstraint(
            "tenant_id",
            "token_hash",
            name="uq_tenant_invitations_token_hash",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_invitations_tenant",
        ),
        ForeignKeyConstraint(
            ["invited_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_invitations_scope_inviter",
        ),
        ForeignKeyConstraint(
            ["accepted_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_invitations_scope_acceptor",
        ),
        ForeignKeyConstraint(
            ["revoked_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_invitations_scope_revoker",
        ),
        ForeignKeyConstraint(
            ["updated_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_invitations_scope_updater",
        ),
        CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'member')",
            name="ck_tenant_invitations_role",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')",
            name="ck_tenant_invitations_status",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_tenant_invitations_revision_positive",
        ),
        CheckConstraint(
            "send_count > 0",
            name="ck_tenant_invitations_send_count_positive",
        ),
        CheckConstraint(
            "(status = 'pending' AND pending_email_key IS NOT NULL "
            "AND pending_email_key = normalized_email) OR "
            "(status <> 'pending' AND pending_email_key IS NULL)",
            name="ck_tenant_invitations_pending_email_key",
        ),
        CheckConstraint(
            "status <> 'accepted' OR (accepted_at IS NOT NULL AND accepted_by IS NOT NULL)",
            name="ck_tenant_invitations_accepted_evidence",
        ),
        CheckConstraint(
            "status <> 'revoked' OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
            name="ck_tenant_invitations_revoked_evidence",
        ),
        Index(
            "ix_tenant_invitations_tenant_status_expires",
            "tenant_id",
            "status",
            "expires_at",
            "id",
        ),
        Index(
            "ix_tenant_invitations_tenant_email",
            "tenant_id",
            "normalized_email",
            "id",
        ),
        Index(
            "ix_tenant_invitations_tenant_status_updated",
            "tenant_id",
            "status",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    email: Mapped[str] = mapped_column(String(256))
    normalized_email: Mapped[str] = mapped_column(String(256), default=_default_normalized_email)
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    token_hash: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(_datetime6())
    accepted_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    accepted_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    invited_by: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    pending_email_key: Mapped[Optional[str]] = mapped_column(String(256), default=None)
    last_sent_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    send_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    revoked_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    revoked_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    updated_by: Mapped[str] = mapped_column(String(64))


class TenantControlMutationRequest(Base):
    """Generic replay-safe tenant control-plane mutation ledger."""

    __tablename__ = "tenant_control_mutation_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "actor_id",
            "idempotency_key",
            name="uq_tenant_control_mutation_requests_actor_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_control_mutation_requests_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_id"],
            ["accounts.id"],
            name="fk_tenant_control_mutation_requests_actor",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_tenant_control_mutation_requests_status",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_tenant_control_mutation_requests_idempotency_digest",
        ),
        CheckConstraint(
            "length(request_hash) = 64",
            name="ck_tenant_control_mutation_requests_request_hash",
        ),
        Index(
            "ix_tenant_control_mutation_requests_tenant_status_created",
            "tenant_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_tenant_control_mutation_requests_tenant_actor_created",
            "tenant_id",
            "actor_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_tenant_control_mutation_requests_tenant_resource_created",
            "tenant_id",
            "resource_type",
            "resource_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    response_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    http_status: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)


class TenantVerifiedDomain(Base):
    __tablename__ = "tenant_verified_domains"
    __table_args__ = (
        UniqueConstraint("normalized_domain", name="uq_tenant_verified_domains_global_domain"),
        UniqueConstraint("tenant_id", "id", name="uq_tenant_verified_domains_scope_id"),
        CheckConstraint(
            "status IN ('pending','verified','revoked')", name="ck_tenant_verified_domains_status"
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_verified_domains_tenant"
        ),
        Index(
            "ix_tenant_verified_domains_tenant_status_updated",
            "tenant_id",
            "status",
            "updated_at",
            "id",
        ),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    normalized_domain: Mapped[str] = mapped_column(String(253))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    verification_method: Mapped[str] = mapped_column(String(16), default="dns_txt")
    challenge_token: Mapped[str] = mapped_column(String(128))
    txt_host: Mapped[str] = mapped_column(String(320))
    txt_value: Mapped[str] = mapped_column(String(512))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    verified_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    verified_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    revoked_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_by: Mapped[str] = mapped_column(String(64))


class TenantIdentityProvider(Base):
    __tablename__ = "tenant_identity_providers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "active_slot", name="uq_tenant_identity_providers_active_slot"
        ),
        CheckConstraint(
            "provider_type IN ('oidc','saml')", name="ck_tenant_identity_providers_type"
        ),
        CheckConstraint(
            "status IN ('draft','active','disabled')", name="ck_tenant_identity_providers_status"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "trusted_domain_id"],
            ["tenant_verified_domains.tenant_id", "tenant_verified_domains.id"],
            name="fk_tenant_identity_providers_domain",
        ),
        Index(
            "ix_tenant_identity_providers_tenant_status", "tenant_id", "status", "updated_at", "id"
        ),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    provider_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    active_slot: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    trusted_domain_id: Mapped[str] = mapped_column(String(64))
    issuer_url: Mapped[Optional[str]] = mapped_column(String(1024), default=None)
    client_id: Mapped[Optional[str]] = mapped_column(String(256), default=None)
    secret_ref: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    scopes: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    entity_id: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    sso_url: Mapped[Optional[str]] = mapped_column(String(1024), default=None)
    metadata_url: Mapped[Optional[str]] = mapped_column(String(1024), default=None)
    certificate_fingerprint: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    validation_state: Mapped[str] = mapped_column(String(16), default="unchecked")
    last_validated_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    validation_error: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    metadata_hash: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_by: Mapped[str] = mapped_column(String(64))
    activated_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    activated_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    disabled_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    disabled_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class TenantScimToken(Base):
    __tablename__ = "tenant_scim_tokens"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_scim_tokens_scope_id"),
        UniqueConstraint("tenant_id", "active_name_key", name="uq_tenant_scim_tokens_active_name"),
        UniqueConstraint("tenant_id", "token_hash", name="uq_tenant_scim_tokens_hash"),
        CheckConstraint(
            "status IN ('active','revoked','expired')", name="ck_tenant_scim_tokens_status"
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tenant_scim_tokens_tenant"),
        Index(
            "ix_tenant_scim_tokens_tenant_status_expires", "tenant_id", "status", "expires_at", "id"
        ),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    active_name_key: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    token_hash: Mapped[str] = mapped_column(String(64))
    token_prefix: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active")
    scopes: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(_datetime6())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    last_used_ip_hash: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    use_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    issued_at: Mapped[datetime] = mapped_column(_datetime6())
    issued_by: Mapped[str] = mapped_column(String(64))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    revoked_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class TenantScimUserLink(Base):
    __tablename__ = "tenant_scim_user_links"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_tenant_scim_user_links_external_id"),
        UniqueConstraint("tenant_id", "user_name", name="uq_tenant_scim_user_links_user_name"),
        UniqueConstraint("tenant_id", "account_id", name="uq_tenant_scim_user_links_account_id"),
        ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_scim_user_links_account_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_token_id"],
            ["tenant_scim_tokens.tenant_id", "tenant_scim_tokens.id"],
            name="fk_tenant_scim_user_links_source_token",
        ),
        CheckConstraint("revision > 0", name="ck_tenant_scim_user_links_revision_positive"),
        Index("ix_tenant_scim_user_links_tenant_updated", "tenant_id", "updated_at", "id"),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(256))
    user_name: Mapped[str] = mapped_column(String(256))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    last_provisioned_at: Mapped[datetime] = mapped_column(_datetime6())
    source_token_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class TenantScimGroupLink(Base):
    __tablename__ = "tenant_scim_group_links"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_tenant_scim_group_links_external_id"),
        UniqueConstraint(
            "tenant_id", "display_name", name="uq_tenant_scim_group_links_display_name"
        ),
        UniqueConstraint("tenant_id", "group_id", name="uq_tenant_scim_group_links_group_id"),
        ForeignKeyConstraint(
            ["tenant_id", "group_id"],
            ["tenant_groups.tenant_id", "tenant_groups.id"],
            name="fk_tenant_scim_group_links_group_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_token_id"],
            ["tenant_scim_tokens.tenant_id", "tenant_scim_tokens.id"],
            name="fk_tenant_scim_group_links_source_token",
        ),
        CheckConstraint("revision > 0", name="ck_tenant_scim_group_links_revision_positive"),
        Index("ix_tenant_scim_group_links_tenant_updated", "tenant_id", "updated_at", "id"),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    group_id: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(256))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    last_provisioned_at: Mapped[datetime] = mapped_column(_datetime6())
    source_token_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class TenantAuditRetentionPolicy(Base):
    __tablename__ = "tenant_audit_retention_policies"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), unique=True)
    audit_retention_days: Mapped[int] = mapped_column(Integer)
    export_retention_days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(_datetime6())
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(_datetime6())
    updated_by: Mapped[str] = mapped_column(String(64))


class TenantAuditLegalHold(Base):
    __tablename__ = "tenant_audit_legal_holds"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    active_name_key: Mapped[Optional[str]] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(_datetime6())
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(_datetime6())
    updated_by: Mapped[str] = mapped_column(String(64))


class TenantAuditExportJob(Base):
    __tablename__ = "tenant_audit_export_jobs"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    format: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    filters_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer)
    requested_at: Mapped[datetime] = mapped_column(_datetime6())
    requested_by: Mapped[str] = mapped_column(String(64))


class TenantOidcLoginTransaction(Base):
    __tablename__ = "tenant_oidc_login_transactions"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[str] = mapped_column(String(64))
    state_digest: Mapped[str] = mapped_column(String(64), unique=True)
    nonce_digest: Mapped[str] = mapped_column(String(64))
    pkce_verifier_ciphertext: Mapped[str] = mapped_column(Text)
    key_version: Mapped[int] = mapped_column(Integer)
    redirect_uri: Mapped[str] = mapped_column(String(1024))
    expires_at: Mapped[datetime] = mapped_column(_datetime6())
    status: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(_datetime6())
    updated_at: Mapped[datetime] = mapped_column(_datetime6())


class TenantOidcSubjectLink(Base):
    __tablename__ = "tenant_oidc_subject_links"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    issuer: Mapped[str] = mapped_column(String(1024))
    subject_digest: Mapped[str] = mapped_column(String(64))
    normalized_email: Mapped[str] = mapped_column(String(256))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(_datetime6())
    updated_at: Mapped[datetime] = mapped_column(_datetime6())


class TenantSsoSession(Base):
    __tablename__ = "tenant_sso_sessions"
    id: Mapped[str] = _pk()
    session_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(_datetime6())
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(_datetime6())
    updated_at: Mapped[datetime] = mapped_column(_datetime6())


class TenantApprovalPolicy(Base):
    """Tenant-scoped approval rule for a high-risk KnowledgeOps action."""

    __tablename__ = "tenant_approval_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_approval_policies_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "active_scope_key",
            name="uq_tenant_approval_policies_active_scope",
        ),
        CheckConstraint(
            "action_type IN ('catalog_upgrade','membership_bootstrap','dataset_acl_disable','member_role_change','identity_provider_disable','audit_retention_execute','workspace_authorization_mode_change','dataset_workspace_transfer','knowledge_base_release_publish','knowledge_base_release_rollback','knowledge_base_release_quality_waiver','document_purge')",
            name="ck_tenant_approval_policies_action_type",
        ),
        CheckConstraint(
            "status IN ('active','disabled')",
            name="ck_tenant_approval_policies_status",
        ),
        CheckConstraint(
            "required_approvals BETWEEN 1 AND 5",
            name="ck_tenant_approval_policies_required_count",
        ),
        CheckConstraint(
            "request_expiry_minutes BETWEEN 15 AND 10080",
            name="ck_tenant_approval_policies_expiry_minutes",
        ),
        CheckConstraint("revision > 0", name="ck_tenant_approval_policies_revision_positive"),
        CheckConstraint(
            "(status='active' AND active_scope_key IS NOT NULL) OR (status='disabled' AND active_scope_key IS NULL)",
            name="ck_tenant_approval_policies_active_scope",
        ),
        CheckConstraint(
            "status <> 'disabled' OR (disabled_at IS NOT NULL AND disabled_by IS NOT NULL)",
            name="ck_tenant_approval_policies_disabled_evidence",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_approval_policies_tenant"
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policies_creator",
        ),
        ForeignKeyConstraint(
            ["updated_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policies_updater",
        ),
        ForeignKeyConstraint(
            ["disabled_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policies_disabler",
        ),
        Index(
            "ix_tenant_approval_policies_tenant_status_action",
            "tenant_id",
            "status",
            "action_type",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    action_type: Mapped[str] = mapped_column(String(64))
    resource_scope: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    active_scope_key: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    status: Mapped[str] = mapped_column(String(16), default="active")
    required_approvals: Mapped[int] = mapped_column(Integer, default=1)
    request_expiry_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    disabled_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    disabled_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class TenantApprovalPolicyApprover(Base):
    """Tenant-safe eligible approver attached to an approval policy."""

    __tablename__ = "tenant_approval_policy_approvers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "policy_id",
            "approver_kind",
            "approver_ref",
            name="uq_tenant_approval_policy_approvers_identity",
        ),
        CheckConstraint(
            "approver_kind IN ('account','role','group')",
            name="ck_tenant_approval_policy_approvers_kind",
        ),
        CheckConstraint(
            "status IN ('active','disabled')",
            name="ck_tenant_approval_policy_approvers_status",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_tenant_approval_policy_approvers_revision_positive",
        ),
        CheckConstraint(
            "(approver_kind='account' AND account_id IS NOT NULL AND group_id IS NULL AND approver_ref=account_id) OR "
            "(approver_kind='group' AND account_id IS NULL AND group_id IS NOT NULL AND approver_ref=group_id) OR "
            "(approver_kind='role' AND account_id IS NULL AND group_id IS NULL AND length(approver_ref) BETWEEN 1 AND 128)",
            name="ck_tenant_approval_policy_approvers_reference",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["tenant_approval_policies.tenant_id", "tenant_approval_policies.id"],
            name="fk_tenant_approval_policy_approvers_scope_policy",
        ),
        ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policy_approvers_scope_account",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "group_id"],
            ["tenant_groups.tenant_id", "tenant_groups.id"],
            name="fk_tenant_approval_policy_approvers_scope_group",
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policy_approvers_creator",
        ),
        ForeignKeyConstraint(
            ["updated_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policy_approvers_updater",
        ),
        Index(
            "ix_tenant_approval_policy_approvers_tenant_policy_status",
            "tenant_id",
            "policy_id",
            "status",
            "approver_kind",
            "id",
        ),
        Index(
            "ix_tenant_approval_policy_approvers_tenant_account",
            "tenant_id",
            "account_id",
            "status",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    policy_id: Mapped[str] = mapped_column(String(64))
    approver_kind: Mapped[str] = mapped_column(String(16))
    approver_ref: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    group_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64))


class TenantApprovalRequest(Base):
    """Persistent approval request with a sanitized snapshot and ticket digest."""

    __tablename__ = "tenant_approval_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_approval_requests_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "requester_id",
            "idempotency_key",
            name="uq_tenant_approval_requests_requester_key",
        ),
        CheckConstraint(
            "action_type IN ('catalog_upgrade','membership_bootstrap','dataset_acl_disable','member_role_change','identity_provider_disable','audit_retention_execute','workspace_authorization_mode_change','dataset_workspace_transfer','knowledge_base_release_publish','knowledge_base_release_rollback','knowledge_base_release_quality_waiver','document_purge')",
            name="ck_tenant_approval_requests_action_type",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','cancelled','expired','executing','executed','execution_failed')",
            name="ck_tenant_approval_requests_status",
        ),
        CheckConstraint(
            "required_approvals BETWEEN 1 AND 5 AND received_approvals BETWEEN 0 AND required_approvals",
            name="ck_tenant_approval_requests_approval_counts",
        ),
        CheckConstraint("revision > 0", name="ck_tenant_approval_requests_revision_positive"),
        CheckConstraint("length(payload_hash)=64", name="ck_tenant_approval_requests_payload_hash"),
        CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_tenant_approval_requests_idempotency_key_length",
        ),
        CheckConstraint(
            "execution_ticket_hash IS NULL OR length(execution_ticket_hash)=64",
            name="ck_tenant_approval_requests_ticket_hash",
        ),
        CheckConstraint(
            "status <> 'rejected' OR (rejected_at IS NOT NULL AND rejected_by IS NOT NULL AND rejection_comment IS NOT NULL AND length(rejection_comment) BETWEEN 1 AND 500)",
            name="ck_tenant_approval_requests_rejected_evidence",
        ),
        CheckConstraint(
            "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL)",
            name="ck_tenant_approval_requests_cancelled_evidence",
        ),
        CheckConstraint(
            "status <> 'executing' OR (ticket_consumed_at IS NOT NULL AND execution_ticket_hash IS NOT NULL)",
            name="ck_tenant_approval_requests_executing_evidence",
        ),
        CheckConstraint(
            "status <> 'executed' OR (executed_at IS NOT NULL AND executed_by IS NOT NULL AND execution_ticket_hash IS NOT NULL)",
            name="ck_tenant_approval_requests_executed_evidence",
        ),
        CheckConstraint(
            "status <> 'execution_failed' OR (execution_failed_at IS NOT NULL AND execution_failed_by IS NOT NULL AND execution_error IS NOT NULL AND length(execution_error) BETWEEN 1 AND 512)",
            name="ck_tenant_approval_requests_execution_failed_evidence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["tenant_approval_policies.tenant_id", "tenant_approval_policies.id"],
            name="fk_tenant_approval_requests_scope_policy",
        ),
        ForeignKeyConstraint(
            ["requester_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_scope_requester",
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_creator",
        ),
        ForeignKeyConstraint(
            ["updated_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_updater",
        ),
        ForeignKeyConstraint(
            ["rejected_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_rejector",
        ),
        ForeignKeyConstraint(
            ["cancelled_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_canceller",
        ),
        ForeignKeyConstraint(
            ["executed_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_executor",
        ),
        ForeignKeyConstraint(
            ["execution_failed_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_failure_actor",
        ),
        Index(
            "ix_tenant_approval_requests_tenant_status_expiry",
            "tenant_id",
            "status",
            "expires_at",
            "id",
        ),
        Index(
            "ix_tenant_approval_requests_tenant_requester_status",
            "tenant_id",
            "requester_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_tenant_approval_requests_tenant_action_resource",
            "tenant_id",
            "action_type",
            "resource_type",
            "resource_id",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    policy_id: Mapped[str] = mapped_column(String(64))
    requester_id: Mapped[str] = mapped_column(String(64))
    action_type: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    required_approvals: Mapped[int] = mapped_column(Integer)
    received_approvals: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(_datetime6())
    revision: Mapped[int] = mapped_column(Integer, default=1)
    execution_ticket_hash: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    ticket_issued_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    ticket_consumed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    rejected_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    rejection_comment: Mapped[Optional[str]] = mapped_column(String(500), default=None)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    cancelled_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    executed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    executed_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    execution_failed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    execution_failed_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    execution_error: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64))


class TenantApprovalDecision(Base):
    """Immutable decision fact recorded for one request and approver."""

    __tablename__ = "tenant_approval_decisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "request_id",
            "approver_id",
            name="uq_tenant_approval_decisions_request_approver",
        ),
        CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_tenant_approval_decisions_decision",
        ),
        CheckConstraint(
            "length(comment) <= 500",
            name="ck_tenant_approval_decisions_comment_length",
        ),
        CheckConstraint(
            "decision <> 'rejected' OR length(comment) BETWEEN 1 AND 500",
            name="ck_tenant_approval_decisions_rejection_comment",
        ),
        CheckConstraint("revision > 0", name="ck_tenant_approval_decisions_revision_positive"),
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            ["tenant_approval_requests.tenant_id", "tenant_approval_requests.id"],
            name="fk_tenant_approval_decisions_scope_request",
        ),
        ForeignKeyConstraint(
            ["approver_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_decisions_scope_approver",
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_decisions_creator",
        ),
        Index(
            "ix_tenant_approval_decisions_tenant_request_time",
            "tenant_id",
            "request_id",
            "decided_at",
            "id",
        ),
        Index(
            "ix_tenant_approval_decisions_tenant_approver_time",
            "tenant_id",
            "approver_id",
            "decided_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64))
    approver_id: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str] = mapped_column(String(500), default="")
    decided_at: Mapped[datetime] = mapped_column(_datetime6())
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))


class App(Base):
    """租户下的应用（问答 / API / 智能体等接入形态）。"""

    __tablename__ = "apps"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_apps_tenant_id"),)

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), default="chat")  # chat | api | agent
    created_at: Mapped[datetime] = _now()

    tenant: Mapped["Tenant"] = relationship(back_populates="apps")
    workflows: Mapped[list["Workflow"]] = relationship(back_populates="app")
    dataset_references: Mapped[list["AppDatasetReference"]] = relationship(
        back_populates="app",
        foreign_keys=lambda: [AppDatasetReference.tenant_id, AppDatasetReference.app_id],
        overlaps="app_dataset_references,app_references,tenant,dataset",
    )


class Workflow(Base):
    """应用下的工作流（检索策略编排配置）。"""

    __tablename__ = "workflows"

    id: Mapped[str] = _pk()
    app_id: Mapped[str] = mapped_column(ForeignKey("apps.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    config: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _now()

    app: Mapped["App"] = relationship(back_populates="workflows")


class Dataset(Base):
    """知识库产品配置、生命周期与用量的权威真账。"""

    __tablename__ = "datasets"
    __mapper_args__ = {"eager_defaults": False}
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_datasets_tenant_id"),
        ForeignKeyConstraint(
            ["owner_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_datasets_scope_owner_member",
        ),
        CheckConstraint(
            "profile_revision > 0",
            name="ck_datasets_profile_revision_positive",
        ),
        CheckConstraint(
            "visibility IN ('private', 'tenant', 'public')",
            name="ck_datasets_visibility",
        ),
        CheckConstraint(
            "status IN ('active', 'archived', 'disabled')",
            name="ck_datasets_status",
        ),
        Index("ix_datasets_scope_status", "tenant_id", "status", "id"),
        Index("ix_datasets_scope_visibility", "tenant_id", "visibility", "id"),
        Index("ix_datasets_scope_owner", "tenant_id", "owner_id", "id"),
        Index("ix_datasets_scope_updated", "tenant_id", "updated_at", "id"),
        CheckConstraint(
            "mutation_generation >= 0 AND serving_generation >= 0",
            name="ck_datasets_generations_nonnegative",
        ),
        CheckConstraint(
            "acl_mode IN ('tenant_role', 'dataset_acl')",
            name="ck_datasets_acl_mode",
        ),
        CheckConstraint(
            "acl_revision > 0",
            name="ck_datasets_acl_revision_positive",
        ),
        CheckConstraint(
            "release_revision > 0",
            name="ck_datasets_release_revision_positive",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "serving_release_id"],
            ["dataset_release_manifests.tenant_id", "dataset_release_manifests.id"],
            name="fk_datasets_scope_serving_release",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    profile_revision: Mapped[int] = mapped_column(Integer, default=1)
    owner_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    profile_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    parser_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    chunk_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    retrieval_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    retention_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    default_language: Mapped[str] = mapped_column(String(32), default="zh-CN")
    graph_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    qa_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    archived_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    mutation_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    serving_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    serving_release_id: Mapped[Optional[str]] = mapped_column(
        String(64), deferred=True, server_default=FetchedValue()
    )
    release_revision: Mapped[int] = mapped_column(Integer, deferred=True, server_default="1")
    acl_mode: Mapped[str] = mapped_column(
        String(16), default="tenant_role", server_default="tenant_role"
    )
    acl_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    acl_enabled_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    acl_enabled_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    doc_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="datasets")
    documents: Mapped[list["Document"]] = relationship(back_populates="dataset")
    metadata_fields: Mapped[list["MetadataField"]] = relationship(back_populates="dataset")
    workspace_bindings: Mapped[list["TenantWorkspaceDataset"]] = relationship(
        back_populates="dataset",
        foreign_keys=lambda: [TenantWorkspaceDataset.tenant_id, TenantWorkspaceDataset.dataset_id],
        overlaps="dataset_bindings,workspace",
    )
    workspace_ownership: Mapped[Optional["DatasetWorkspaceOwnership"]] = relationship(
        back_populates="dataset",
        uselist=False,
        foreign_keys=lambda: [
            DatasetWorkspaceOwnership.tenant_id,
            DatasetWorkspaceOwnership.dataset_id,
        ],
        overlaps="dataset_ownerships,dataset_workspace_ownerships,tenant,workspace",
    )
    app_references: Mapped[list["AppDatasetReference"]] = relationship(
        back_populates="dataset",
        foreign_keys=lambda: [AppDatasetReference.tenant_id, AppDatasetReference.dataset_id],
        overlaps="app_dataset_references,dataset_references,tenant,app",
    )


class DatasetWorkspaceOwnership(Base):
    """Authoritative tenant-scoped Workspace owner for one Dataset."""

    __tablename__ = "dataset_workspace_ownerships"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_dataset_workspace_ownerships_scope_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            name="uq_dataset_workspace_ownerships_scope_dataset",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_dataset_workspace_ownerships_revision_positive",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_dataset_workspace_ownerships_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_workspace_ownerships_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_dataset_workspace_ownerships_scope_workspace",
        ),
        Index(
            "ix_dataset_workspace_ownerships_tenant_workspace_dataset",
            "tenant_id",
            "workspace_id",
            "dataset_id",
        ),
        Index(
            "ix_dataset_workspace_ownerships_tenant_updated_dataset",
            "tenant_id",
            "updated_at",
            "dataset_id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    workspace_id: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    last_transfer_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)

    tenant: Mapped["Tenant"] = relationship(
        back_populates="dataset_workspace_ownerships",
        foreign_keys=lambda: [DatasetWorkspaceOwnership.tenant_id],
        overlaps="dataset,workspace,workspace_ownership,dataset_ownerships",
    )
    dataset: Mapped["Dataset"] = relationship(
        back_populates="workspace_ownership",
        foreign_keys=lambda: [
            DatasetWorkspaceOwnership.tenant_id,
            DatasetWorkspaceOwnership.dataset_id,
        ],
        overlaps="tenant,workspace,dataset_workspace_ownerships,dataset_ownerships",
    )
    workspace: Mapped["TenantWorkspace"] = relationship(
        back_populates="dataset_ownerships",
        foreign_keys=lambda: [
            DatasetWorkspaceOwnership.tenant_id,
            DatasetWorkspaceOwnership.workspace_id,
        ],
        overlaps="tenant,dataset,dataset_workspace_ownerships,workspace_ownership",
    )


class AppDatasetReference(Base):
    """Durable tenant-scoped Application to Dataset dependency fact."""

    __tablename__ = "app_dataset_references"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_app_dataset_references_scope_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "app_id",
            "dataset_id",
            "active_slot",
            name="uq_app_dataset_references_active_slot",
        ),
        CheckConstraint(
            "reference_kind = 'knowledge'",
            name="ck_app_dataset_references_reference_kind",
        ),
        CheckConstraint(
            "status IN ('active','removed')",
            name="ck_app_dataset_references_status",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_app_dataset_references_revision_positive",
        ),
        CheckConstraint(
            "(status = 'active' AND active_slot = 'active' AND removed_at IS NULL "
            "AND removed_by IS NULL) OR (status = 'removed' AND active_slot IS NULL "
            "AND removed_at IS NOT NULL AND removed_by IS NOT NULL)",
            name="ck_app_dataset_references_lifecycle_evidence",
        ),
        CheckConstraint(
            "(release_mode = 'follow_channel' AND release_channel_id IS NOT NULL AND pinned_release_id IS NULL) OR "
            "(release_mode = 'pinned' AND release_channel_id IS NULL AND pinned_release_id IS NOT NULL)",
            name="ck_app_dataset_references_release_binding",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "release_channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_app_dataset_references_scope_release_channel",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "pinned_release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_app_dataset_references_scope_pinned_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_app_dataset_references_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "app_id"],
            ["apps.tenant_id", "apps.id"],
            name="fk_app_dataset_references_scope_app",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_app_dataset_references_scope_dataset",
        ),
        Index(
            "ix_app_dataset_references_tenant_dataset_status",
            "tenant_id",
            "dataset_id",
            "status",
            "id",
        ),
        Index(
            "ix_app_dataset_references_tenant_app_status",
            "tenant_id",
            "app_id",
            "status",
            "id",
        ),
        Index(
            "ix_app_dataset_references_tenant_status_updated",
            "tenant_id",
            "status",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    app_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    reference_kind: Mapped[str] = mapped_column(String(16), default="knowledge")
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    active_slot: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    removed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    removed_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    request_id: Mapped[str] = mapped_column(String(128))
    release_mode: Mapped[str] = mapped_column(
        String(16), deferred=True, server_default="'follow_channel'"
    )
    release_channel_id: Mapped[Optional[str]] = mapped_column(
        String(128), deferred=True, server_default=FetchedValue()
    )
    pinned_release_id: Mapped[Optional[str]] = mapped_column(
        String(64), deferred=True, server_default=FetchedValue()
    )

    tenant: Mapped["Tenant"] = relationship(
        back_populates="app_dataset_references",
        foreign_keys=lambda: [AppDatasetReference.tenant_id],
        overlaps="app,dataset,dataset_references,app_references",
    )
    app: Mapped["App"] = relationship(
        back_populates="dataset_references",
        foreign_keys=lambda: [AppDatasetReference.tenant_id, AppDatasetReference.app_id],
        overlaps="tenant,dataset,app_dataset_references,app_references",
    )
    dataset: Mapped["Dataset"] = relationship(
        back_populates="app_references",
        foreign_keys=lambda: [AppDatasetReference.tenant_id, AppDatasetReference.dataset_id],
        overlaps="tenant,app,app_dataset_references,dataset_references",
    )


class TenantReleaseChannel(Base):
    """Tenant-scoped release channel used for controlled environment promotion."""

    __tablename__ = "tenant_release_channels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_release_channels_scope_id"),
        UniqueConstraint("tenant_id", "code", name="uq_tenant_release_channels_tenant_code"),
        UniqueConstraint(
            "tenant_id",
            "normalized_code",
            name="uq_tenant_release_channels_tenant_normalized_code",
        ),
        UniqueConstraint(
            "tenant_id",
            "active_default_slot",
            name="uq_tenant_release_channels_active_default",
        ),
        CheckConstraint(
            "status IN ('active','archived')", name="ck_tenant_release_channels_status"
        ),
        CheckConstraint(
            "risk_tier IN ('low','medium','high')", name="ck_tenant_release_channels_risk_tier"
        ),
        CheckConstraint(
            "length(code) BETWEEN 1 AND 64", name="ck_tenant_release_channels_code_length"
        ),
        CheckConstraint(
            "promotion_order >= 0",
            name="ck_tenant_release_channels_promotion_order_nonnegative",
        ),
        CheckConstraint("revision > 0", name="ck_tenant_release_channels_revision_positive"),
        CheckConstraint(
            "(status = 'active' AND archived_at IS NULL AND archived_by IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL AND archived_by IS NOT NULL)",
            name="ck_tenant_release_channels_lifecycle_evidence",
        ),
        CheckConstraint(
            "(is_default_serving = false AND active_default_slot IS NULL) OR "
            "(is_default_serving = true AND status = 'active' AND active_default_slot = 'default')",
            name="ck_tenant_release_channels_active_default_slot",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_release_channels_tenant"
        ),
        Index(
            "ix_tenant_release_channels_tenant_status_order",
            "tenant_id",
            "status",
            "promotion_order",
            "id",
        ),
        Index(
            "ix_tenant_release_channels_tenant_default",
            "tenant_id",
            "status",
            "active_default_slot",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(64))
    normalized_code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="'active'")
    risk_tier: Mapped[str] = mapped_column(String(16))
    promotion_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_default_serving: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    active_default_slot: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    archived_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    archived_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class DatasetReleaseManifest(Base):
    """Immutable snapshot of the effective Dataset authority at release time."""

    __tablename__ = "dataset_release_manifests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_dataset_release_manifests_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            name="uq_dataset_release_manifests_scope_dataset_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "release_number",
            name="uq_dataset_release_manifests_dataset_number",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "manifest_digest",
            name="uq_dataset_release_manifests_dataset_digest",
        ),
        CheckConstraint(
            "release_number > 0",
            name="ck_dataset_release_manifests_release_number_positive",
        ),
        CheckConstraint(
            "profile_revision > 0 AND ownership_revision > 0 AND workspace_revision > 0",
            name="ck_dataset_release_manifests_revisions_positive",
        ),
        CheckConstraint(
            "mutation_generation >= 0 AND serving_generation >= 0",
            name="ck_dataset_release_manifests_generations_nonnegative",
        ),
        CheckConstraint(
            "schema_version > 0", name="ck_dataset_release_manifests_schema_version_positive"
        ),
        CheckConstraint(
            "length(policy_digest) = 64 AND lower(policy_digest) = policy_digest",
            name="ck_dataset_release_manifests_policy_digest",
        ),
        CheckConstraint(
            "length(manifest_digest) = 64 AND lower(manifest_digest) = manifest_digest",
            name="ck_dataset_release_manifests_manifest_digest",
        ),
        CheckConstraint(
            "length(readiness_digest) = 64 AND lower(readiness_digest) = readiness_digest",
            name="ck_dataset_release_manifests_readiness_digest",
        ),
        CheckConstraint(
            "readiness_state IN ('ready','blocked','unavailable')",
            name="ck_dataset_release_manifests_readiness_state",
        ),
        CheckConstraint(
            "entry_count >= 0 AND blocker_count >= 0",
            name="ck_dataset_release_manifests_counts_nonnegative",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_manifests_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_release_manifests_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_dataset_release_manifests_scope_workspace",
        ),
        Index(
            "ix_dataset_release_manifests_tenant_dataset_number",
            "tenant_id",
            "dataset_id",
            "release_number",
            "id",
        ),
        Index(
            "ix_dataset_release_manifests_tenant_dataset_created",
            "tenant_id",
            "dataset_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_dataset_release_manifests_tenant_readiness",
            "tenant_id",
            "readiness_state",
            "created_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    release_number: Mapped[int] = mapped_column(Integer)
    profile_revision: Mapped[int] = mapped_column(Integer)
    ownership_revision: Mapped[int] = mapped_column(Integer)
    workspace_id: Mapped[str] = mapped_column(String(128))
    workspace_revision: Mapped[int] = mapped_column(Integer)
    mutation_generation: Mapped[int] = mapped_column(BigInteger)
    serving_generation: Mapped[int] = mapped_column(BigInteger)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    policy_digest: Mapped[str] = mapped_column(String(64))
    manifest_digest: Mapped[str] = mapped_column(String(64))
    readiness_digest: Mapped[str] = mapped_column(String(64))
    readiness_state: Mapped[str] = mapped_column(String(16))
    entry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    blocker_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    readiness_blockers_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(512))
    request_id: Mapped[str] = mapped_column(String(128))


class DatasetReleaseEntry(Base):
    """Immutable, body-free authority reference contained in one release manifest."""

    __tablename__ = "dataset_release_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_dataset_release_entries_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "release_id",
            "ordinal",
            name="uq_dataset_release_entries_release_ordinal",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "release_id",
            "resource_type",
            "resource_id",
            name="uq_dataset_release_entries_resource",
        ),
        CheckConstraint("ordinal >= 0", name="ck_dataset_release_entries_ordinal_nonnegative"),
        CheckConstraint(
            "resource_type IN ('dataset_profile','document_version','qa_revision','source_generation','projection_revision')",
            name="ck_dataset_release_entries_resource_type",
        ),
        CheckConstraint(
            "resource_revision >= 0",
            name="ck_dataset_release_entries_resource_revision_nonnegative",
        ),
        CheckConstraint(
            "content_digest IS NULL OR (length(content_digest) = 64 AND lower(content_digest) = content_digest)",
            name="ck_dataset_release_entries_content_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_entries_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_release_entries_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_release_entries_scope_release",
        ),
        Index(
            "ix_dataset_release_entries_tenant_release_ordinal",
            "tenant_id",
            "dataset_id",
            "release_id",
            "ordinal",
            "id",
        ),
        Index(
            "ix_dataset_release_entries_tenant_resource",
            "tenant_id",
            "dataset_id",
            "resource_type",
            "resource_id",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    release_id: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(String(128))
    resource_revision: Mapped[int] = mapped_column(BigInteger)
    content_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    safe_facts_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )


class DatasetReleaseEvent(Base):
    """Append-only audit event for release capture and channel lifecycle changes."""

    __tablename__ = "dataset_release_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_dataset_release_events_scope_id"),
        CheckConstraint(
            "event_type IN ('candidate_created','promoted','superseded','rolled_back','retired')",
            name="ck_dataset_release_events_event_type",
        ),
        CheckConstraint(
            "length(reason) BETWEEN 1 AND 512",
            name="ck_dataset_release_events_reason_length",
        ),
        CheckConstraint(
            "previous_binding_revision IS NULL OR previous_binding_revision >= 0",
            name="ck_dataset_release_events_previous_revision_nonnegative",
        ),
        CheckConstraint(
            "current_binding_revision IS NULL OR current_binding_revision >= 0",
            name="ck_dataset_release_events_current_revision_nonnegative",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_events_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_release_events_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_release_events_scope_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_dataset_release_events_scope_channel",
        ),
        Index(
            "ix_dataset_release_events_tenant_release_time",
            "tenant_id",
            "dataset_id",
            "release_id",
            "occurred_at",
            "id",
        ),
        Index(
            "ix_dataset_release_events_tenant_channel_time",
            "tenant_id",
            "channel_id",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    release_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    event_type: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(512))
    request_id: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    previous_binding_revision: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    current_binding_revision: Mapped[Optional[int]] = mapped_column(Integer, default=None)


class DatasetChannelRelease(Base):
    """Current or removed release binding for one Dataset and release channel."""

    __tablename__ = "dataset_channel_releases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_dataset_channel_releases_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "channel_id",
            name="uq_dataset_channel_releases_scope_channel",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "channel_id",
            "active_slot",
            name="uq_dataset_channel_releases_active_slot",
        ),
        CheckConstraint(
            "status IN ('active','removed')", name="ck_dataset_channel_releases_status"
        ),
        CheckConstraint("revision > 0", name="ck_dataset_channel_releases_revision_positive"),
        CheckConstraint(
            "(status = 'active' AND active_slot = 'active' AND active_release_id IS NOT NULL "
            "AND activated_at IS NOT NULL AND activated_by IS NOT NULL) OR "
            "(status = 'removed' AND active_slot IS NULL AND active_release_id IS NULL)",
            name="ck_dataset_channel_releases_active_slot",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_channel_releases_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_channel_releases_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_dataset_channel_releases_scope_channel",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "active_release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_channel_releases_scope_active_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "previous_release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_channel_releases_scope_previous_release",
        ),
        Index(
            "ix_dataset_channel_releases_tenant_dataset_status",
            "tenant_id",
            "dataset_id",
            "status",
            "revision",
            "id",
        ),
        Index(
            "ix_dataset_channel_releases_tenant_channel_status",
            "tenant_id",
            "channel_id",
            "status",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(128))
    active_release_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    previous_release_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="'active'")
    active_slot: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    activated_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    activated_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    request_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by: Mapped[str] = mapped_column(String(64))


class TenantReleaseQualityGatePolicy(Base):
    """Tenant-scoped revision-fenced Release quality gate policy."""

    __tablename__ = "tenant_release_quality_gate_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_release_quality_gate_policies_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "active_scope_key",
            name="uq_tenant_release_quality_gate_policies_active_scope",
        ),
        CheckConstraint(
            "scope_type IN ('global','risk_tier','channel')",
            name="ck_tenant_release_quality_gate_policies_scope_type",
        ),
        CheckConstraint(
            "status IN ('active','disabled')", name="ck_tenant_release_quality_gate_policies_status"
        ),
        CheckConstraint("revision > 0", name="ck_tenant_release_quality_gate_policies_revision"),
        CheckConstraint(
            "min_experiment_count > 0 AND min_judged_result_count >= 0 AND "
            "min_judgment_coverage_bps BETWEEN 0 AND 10000 AND "
            "min_exact_agreement_bps BETWEEN 0 AND 10000 AND "
            "min_mean_score_milli BETWEEN 0 AND 3000 AND "
            "max_conflicting_results >= 0 AND max_certification_age_minutes > 0",
            name="ck_tenant_release_quality_gate_policies_thresholds",
        ),
        CheckConstraint(
            "(scope_type='global' AND scope_value='*' AND channel_id IS NULL) OR "
            "(scope_type='risk_tier' AND scope_value IN ('low','medium','high') AND channel_id IS NULL) OR "
            "(scope_type='channel' AND channel_id IS NOT NULL AND scope_value=channel_id)",
            name="ck_tenant_release_quality_gate_policies_scope_binding",
        ),
        CheckConstraint(
            _quality_policy_active_scope_key_check(),
            name="ck_tenant_release_quality_gate_policies_active_scope_key",
        ),
        CheckConstraint(
            "(status='active' AND active_scope_key IS NOT NULL AND disabled_at IS NULL AND disabled_by IS NULL) OR "
            "(status='disabled' AND active_scope_key IS NULL AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL)",
            name="ck_tenant_release_quality_gate_policies_lifecycle",
        ),
        CheckConstraint(
            "length(policy_digest)=64 AND lower(policy_digest)=policy_digest",
            name="ck_tenant_release_quality_gate_policies_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_release_quality_gate_policies_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_tenant_release_quality_gate_policies_scope_channel",
        ),
        Index(
            "ix_tenant_release_quality_gate_policies_scope_status",
            "tenant_id",
            "scope_type",
            "scope_value",
            "status",
            "id",
        ),
        Index(
            "ix_tenant_release_quality_gate_policies_tenant_updated",
            "tenant_id",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_value: Mapped[str] = mapped_column(String(128))
    channel_id: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    active_scope_key: Mapped[Optional[str]] = mapped_column(String(192), default=None)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="'active'")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    min_experiment_count: Mapped[int] = mapped_column(Integer)
    min_judged_result_count: Mapped[int] = mapped_column(Integer)
    min_judgment_coverage_bps: Mapped[int] = mapped_column(Integer)
    min_exact_agreement_bps: Mapped[int] = mapped_column(Integer)
    min_mean_score_milli: Mapped[int] = mapped_column(Integer)
    max_conflicting_results: Mapped[int] = mapped_column(Integer)
    require_all_experiments_completed: Mapped[bool] = mapped_column(Boolean)
    require_no_degraded_results: Mapped[bool] = mapped_column(Boolean)
    max_certification_age_minutes: Mapped[int] = mapped_column(Integer)
    policy_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_by: Mapped[str] = mapped_column(String(64))
    disabled_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    disabled_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class DatasetQualityBaseline(Base):
    __tablename__ = "dataset_quality_baselines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_dataset_quality_baselines_scope_id"),
        UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_dataset_quality_baselines_scope_dataset_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "normalized_name",
            "baseline_revision",
            name="uq_dataset_quality_baselines_name_revision",
        ),
        UniqueConstraint(
            "tenant_id", "dataset_id", "baseline_digest", name="uq_dataset_quality_baselines_digest"
        ),
        CheckConstraint("baseline_revision > 0", name="ck_dataset_quality_baselines_revision"),
        CheckConstraint(
            "experiment_count > 0 AND query_count > 0", name="ck_dataset_quality_baselines_counts"
        ),
        CheckConstraint(
            "length(baseline_digest)=64 AND lower(baseline_digest)=baseline_digest",
            name="ck_dataset_quality_baselines_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_quality_baselines_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_quality_baselines_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "parent_baseline_id"],
            [
                "dataset_quality_baselines.tenant_id",
                "dataset_quality_baselines.dataset_id",
                "dataset_quality_baselines.id",
            ],
            name="fk_dataset_quality_baselines_scope_parent",
        ),
        Index(
            "ix_dataset_quality_baselines_scope_name_revision",
            "tenant_id",
            "dataset_id",
            "normalized_name",
            "baseline_revision",
            "id",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(256))
    baseline_revision: Mapped[int] = mapped_column(Integer)
    parent_baseline_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    experiment_count: Mapped[int] = mapped_column(Integer)
    query_count: Mapped[int] = mapped_column(Integer)
    baseline_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(512))
    request_id: Mapped[str] = mapped_column(String(128))


class DatasetQualityBaselineItem(Base):
    __tablename__ = "dataset_quality_baseline_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_dataset_quality_baseline_items_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            name="uq_dataset_quality_baseline_items_scope_dataset_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "baseline_id",
            "ordinal",
            name="uq_dataset_quality_baseline_items_ordinal",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "baseline_id",
            "experiment_id",
            name="uq_dataset_quality_baseline_items_experiment",
        ),
        CheckConstraint("ordinal > 0", name="ck_dataset_quality_baseline_items_ordinal"),
        CheckConstraint(
            "experiment_sequence > 0 AND experiment_serving_generation >= 0",
            name="ck_dataset_quality_baseline_items_authority",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_quality_baseline_items_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "baseline_id"],
            [
                "dataset_quality_baselines.tenant_id",
                "dataset_quality_baselines.dataset_id",
                "dataset_quality_baselines.id",
            ],
            name="fk_dataset_quality_baseline_items_scope_baseline",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "experiment_id"],
            [
                "retrieval_experiments.tenant_id",
                "retrieval_experiments.dataset_id",
                "retrieval_experiments.id",
            ],
            name="fk_dataset_quality_baseline_items_scope_experiment",
        ),
        Index(
            "ix_dataset_quality_baseline_items_scope_ordinal",
            "tenant_id",
            "dataset_id",
            "baseline_id",
            "ordinal",
            "id",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    baseline_id: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    experiment_id: Mapped[str] = mapped_column(String(64))
    experiment_sequence: Mapped[int] = mapped_column(BigInteger)
    query_hash: Mapped[str] = mapped_column(String(64))
    experiment_serving_generation: Mapped[int] = mapped_column(BigInteger)
    strategy_digest: Mapped[str] = mapped_column(String(64))
    result_digest: Mapped[str] = mapped_column(String(64))
    evidence_digest: Mapped[str] = mapped_column(String(64))
    judgment_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class DatasetReleaseQualityCertification(Base):
    __tablename__ = "dataset_release_quality_certifications"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_dataset_release_quality_certifications_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            name="uq_dataset_release_quality_certifications_dataset_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "release_id",
            "baseline_id",
            "policy_id",
            "policy_revision",
            "evidence_digest",
            name="uq_dataset_release_quality_certifications_authority",
        ),
        CheckConstraint(
            "status IN ('passed','failed')", name="ck_dataset_release_quality_certifications_status"
        ),
        CheckConstraint(
            "policy_revision > 0 AND release_mutation_generation >= 0 AND release_serving_generation >= 0",
            name="ck_dataset_release_quality_certifications_revisions",
        ),
        CheckConstraint(
            "experiment_count > 0 AND completed_experiment_count >= 0 AND degraded_experiment_count >= 0 AND query_count > 0 AND judged_result_count >= 0 AND total_result_count >= 0 AND judgment_count >= 0 AND multi_judged_results >= 0 AND unanimous_results >= 0 AND conflicting_results >= 0 AND failed_rule_count >= 0",
            name="ck_dataset_release_quality_certifications_counts",
        ),
        CheckConstraint(
            "judgment_coverage_bps IS NULL OR judgment_coverage_bps BETWEEN 0 AND 10000",
            name="ck_dataset_release_quality_certifications_coverage",
        ),
        CheckConstraint(
            "exact_agreement_bps IS NULL OR exact_agreement_bps BETWEEN 0 AND 10000",
            name="ck_dataset_release_quality_certifications_agreement",
        ),
        CheckConstraint(
            "mean_score_milli IS NULL OR mean_score_milli BETWEEN 0 AND 3000",
            name="ck_dataset_release_quality_certifications_score",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_quality_certifications_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_release_quality_certifications_scope_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "baseline_id"],
            [
                "dataset_quality_baselines.tenant_id",
                "dataset_quality_baselines.dataset_id",
                "dataset_quality_baselines.id",
            ],
            name="fk_dataset_release_quality_certifications_scope_baseline",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "tenant_release_quality_gate_policies.tenant_id",
                "tenant_release_quality_gate_policies.id",
            ],
            name="fk_dataset_release_quality_certifications_scope_policy",
        ),
        Index(
            "ix_dataset_release_quality_certifications_scope_release_time",
            "tenant_id",
            "dataset_id",
            "release_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_dataset_release_quality_certifications_scope_status_valid",
            "tenant_id",
            "dataset_id",
            "status",
            "valid_until",
            "id",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    release_id: Mapped[str] = mapped_column(String(64))
    baseline_id: Mapped[str] = mapped_column(String(64))
    policy_id: Mapped[str] = mapped_column(String(64))
    policy_revision: Mapped[int] = mapped_column(Integer)
    release_manifest_digest: Mapped[str] = mapped_column(String(64))
    release_mutation_generation: Mapped[int] = mapped_column(BigInteger)
    release_serving_generation: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16))
    experiment_count: Mapped[int] = mapped_column(Integer)
    completed_experiment_count: Mapped[int] = mapped_column(Integer)
    degraded_experiment_count: Mapped[int] = mapped_column(Integer)
    query_count: Mapped[int] = mapped_column(Integer)
    judged_result_count: Mapped[int] = mapped_column(Integer)
    total_result_count: Mapped[int] = mapped_column(Integer)
    judgment_count: Mapped[int] = mapped_column(Integer)
    judgment_coverage_bps: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    multi_judged_results: Mapped[int] = mapped_column(Integer)
    unanimous_results: Mapped[int] = mapped_column(Integer)
    conflicting_results: Mapped[int] = mapped_column(Integer)
    exact_agreement_bps: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    mean_score_milli: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    failed_rule_count: Mapped[int] = mapped_column(Integer)
    policy_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence_digest: Mapped[str] = mapped_column(String(64))
    certification_digest: Mapped[str] = mapped_column(String(64))
    valid_until: Mapped[datetime] = mapped_column(_datetime6())
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(512))
    request_id: Mapped[str] = mapped_column(String(128))


class DatasetReleaseQualityCertificationEvidence(Base):
    __tablename__ = "dataset_release_quality_certification_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_dataset_release_quality_certification_evidence_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "certification_id",
            "ordinal",
            name="uq_dataset_release_quality_certification_evidence_ordinal",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "certification_id",
            "baseline_item_id",
            name="uq_dataset_release_quality_certification_evidence_item",
        ),
        CheckConstraint(
            "ordinal > 0", name="ck_dataset_release_quality_certification_evidence_ordinal"
        ),
        CheckConstraint(
            "status IN ('completed','failed')",
            name="ck_dataset_release_quality_certification_evidence_status",
        ),
        CheckConstraint(
            "result_count >= 0 AND judged_result_count >= 0 AND judgment_count >= 0 AND multi_judged_results >= 0 AND unanimous_results >= 0 AND conflicting_results >= 0",
            name="ck_dataset_release_quality_certification_evidence_counts",
        ),
        CheckConstraint(
            "exact_agreement_bps IS NULL OR exact_agreement_bps BETWEEN 0 AND 10000",
            name="ck_dataset_release_quality_certification_evidence_agreement",
        ),
        CheckConstraint(
            "mean_score_milli IS NULL OR mean_score_milli BETWEEN 0 AND 3000",
            name="ck_dataset_release_quality_certification_evidence_score",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_dataset_release_quality_certification_evidence_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "certification_id"],
            [
                "dataset_release_quality_certifications.tenant_id",
                "dataset_release_quality_certifications.dataset_id",
                "dataset_release_quality_certifications.id",
            ],
            name="fk_quality_cert_evidence_scope_certification",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "baseline_item_id"],
            [
                "dataset_quality_baseline_items.tenant_id",
                "dataset_quality_baseline_items.dataset_id",
                "dataset_quality_baseline_items.id",
            ],
            name="fk_dataset_release_quality_certification_evidence_scope_item",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "experiment_id"],
            [
                "retrieval_experiments.tenant_id",
                "retrieval_experiments.dataset_id",
                "retrieval_experiments.id",
            ],
            name="fk_quality_cert_evidence_scope_experiment",
        ),
        Index(
            "ix_dataset_release_quality_certification_evidence_scope_ordinal",
            "tenant_id",
            "dataset_id",
            "certification_id",
            "ordinal",
            "id",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    certification_id: Mapped[str] = mapped_column(String(64))
    baseline_item_id: Mapped[str] = mapped_column(String(64))
    experiment_id: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    result_count: Mapped[int] = mapped_column(Integer)
    judged_result_count: Mapped[int] = mapped_column(Integer)
    judgment_count: Mapped[int] = mapped_column(Integer)
    multi_judged_results: Mapped[int] = mapped_column(Integer)
    unanimous_results: Mapped[int] = mapped_column(Integer)
    conflicting_results: Mapped[int] = mapped_column(Integer)
    exact_agreement_bps: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    mean_score_milli: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    experiment_digest: Mapped[str] = mapped_column(String(64))
    judgment_digest: Mapped[str] = mapped_column(String(64))
    safe_facts_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class DatasetReleaseQualityWaiver(Base):
    __tablename__ = "dataset_release_quality_waivers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_dataset_release_quality_waivers_scope_id"),
        UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_dataset_release_quality_waivers_dataset_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "approval_request_id",
            name="uq_dataset_release_quality_waivers_approval_request",
        ),
        UniqueConstraint(
            "tenant_id",
            "approval_execution_id",
            name="uq_dataset_release_quality_waivers_approval_execution",
        ),
        CheckConstraint(
            "policy_revision > 0", name="ck_dataset_release_quality_waivers_policy_revision"
        ),
        CheckConstraint(
            "expires_at > valid_from", name="ck_dataset_release_quality_waivers_window"
        ),
        CheckConstraint(
            "length(waiver_digest)=64 AND lower(waiver_digest)=waiver_digest",
            name="ck_dataset_release_quality_waivers_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_quality_waivers_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_release_quality_waivers_scope_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_dataset_release_quality_waivers_scope_channel",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "tenant_release_quality_gate_policies.tenant_id",
                "tenant_release_quality_gate_policies.id",
            ],
            name="fk_dataset_release_quality_waivers_scope_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            ["tenant_approval_requests.tenant_id", "tenant_approval_requests.id"],
            name="fk_dataset_release_quality_waivers_scope_approval",
        ),
        Index(
            "ix_dataset_release_quality_waivers_scope_release_channel",
            "tenant_id",
            "dataset_id",
            "release_id",
            "channel_id",
            "expires_at",
            "id",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    release_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(128))
    policy_id: Mapped[str] = mapped_column(String(64))
    policy_revision: Mapped[int] = mapped_column(Integer)
    release_manifest_digest: Mapped[str] = mapped_column(String(64))
    approval_request_id: Mapped[str] = mapped_column(String(64))
    approval_execution_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(512))
    valid_from: Mapped[datetime] = mapped_column(_datetime6())
    expires_at: Mapped[datetime] = mapped_column(_datetime6())
    waiver_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128))


class DatasetReleaseQualityEvent(Base):
    __tablename__ = "dataset_release_quality_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_dataset_release_quality_events_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "release_id",
            "channel_id",
            "event_sequence",
            name="uq_dataset_release_quality_events_stream_sequence",
        ),
        CheckConstraint("event_sequence > 0", name="ck_dataset_release_quality_events_sequence"),
        CheckConstraint(
            "event_type IN ('certification_created','gate_evaluated','gate_passed','gate_failed','gate_blocked','gate_unavailable','gate_expired','gate_revoked','waiver_requested','waiver_approved','waiver_rejected','waiver_applied','waiver_expired','waiver_revoked','release_promoted_with_quality_gate','release_rolled_back_with_quality_gate')",
            name="ck_dataset_release_quality_events_type",
        ),
        CheckConstraint(
            "state IS NULL OR state IN ('passed','failed','blocked','unavailable','active','expired','revoked')",
            name="ck_dataset_release_quality_events_state",
        ),
        CheckConstraint(
            "previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND lower(previous_event_digest)=previous_event_digest)",
            name="ck_dataset_release_quality_events_previous_digest",
        ),
        CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest",
            name="ck_dataset_release_quality_events_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_quality_events_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_release_quality_events_scope_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_dataset_release_quality_events_scope_channel",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            ["tenant_approval_requests.tenant_id", "tenant_approval_requests.id"],
            name="fk_dataset_release_quality_events_scope_approval",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "certification_id"],
            [
                "dataset_release_quality_certifications.tenant_id",
                "dataset_release_quality_certifications.dataset_id",
                "dataset_release_quality_certifications.id",
            ],
            name="fk_dataset_release_quality_events_scope_certification",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "waiver_id"],
            [
                "dataset_release_quality_waivers.tenant_id",
                "dataset_release_quality_waivers.dataset_id",
                "dataset_release_quality_waivers.id",
            ],
            name="fk_dataset_release_quality_events_scope_waiver",
        ),
        Index(
            "ix_dataset_release_quality_events_scope_stream",
            "tenant_id",
            "dataset_id",
            "release_id",
            "channel_id",
            "event_sequence",
            "id",
        ),
        Index(
            "ix_dataset_release_quality_events_scope_time",
            "tenant_id",
            "dataset_id",
            "occurred_at",
            "id",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    release_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(128))
    certification_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    waiver_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    event_type: Mapped[str] = mapped_column(String(32))
    event_sequence: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    previous_event_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    event_digest: Mapped[str] = mapped_column(String(64))
    approval_request_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    approval_execution_id: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    actor_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(512))
    safe_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    request_id: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class TenantReleaseQualitySloPolicy(Base):
    """Tenant SLO thresholds resolved by Channel, risk tier, then global scope."""

    __tablename__ = "tenant_release_quality_slo_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_release_quality_slo_policies_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "active_scope_key",
            name="uq_tenant_release_quality_slo_policies_active_scope",
        ),
        CheckConstraint(
            "scope_type IN ('global','risk_tier','channel')",
            name="ck_tenant_release_quality_slo_policies_scope_type",
        ),
        CheckConstraint(
            "status IN ('active','disabled')", name="ck_tenant_release_quality_slo_policies_status"
        ),
        CheckConstraint("revision > 0", name="ck_tenant_release_quality_slo_policies_revision"),
        CheckConstraint(
            "certification_warning_minutes > certification_critical_minutes AND certification_critical_minutes > 0 "
            "AND waiver_warning_minutes > 0 AND max_open_alerts > 0",
            name="ck_tenant_release_quality_slo_policies_thresholds",
        ),
        CheckConstraint(
            "auto_queue_recertification IN (false,true) AND require_passing_certification IN (false,true) AND allow_active_waiver IN (false,true)",
            name="ck_tenant_release_quality_slo_policies_flags",
        ),
        CheckConstraint(
            "(scope_type='global' AND scope_value='*' AND channel_id IS NULL) OR "
            "(scope_type='risk_tier' AND scope_value IN ('low','medium','high') AND channel_id IS NULL) OR "
            "(scope_type='channel' AND channel_id IS NOT NULL AND scope_value=channel_id)",
            name="ck_tenant_release_quality_slo_policies_scope_binding",
        ),
        CheckConstraint(
            _quality_policy_active_scope_key_check(),
            name="ck_tenant_release_quality_slo_policies_active_scope_key",
        ),
        CheckConstraint(
            "(status='active' AND active_scope_key IS NOT NULL AND disabled_at IS NULL AND disabled_by IS NULL) OR "
            "(status='disabled' AND active_scope_key IS NULL AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL)",
            name="ck_tenant_release_quality_slo_policies_lifecycle",
        ),
        CheckConstraint(
            "length(policy_digest)=64 AND lower(policy_digest)=policy_digest",
            name="ck_tenant_release_quality_slo_policies_digest",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_quality_slo_policies_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_quality_slo_policies_channel",
        ),
        Index(
            "ix_quality_slo_policies_scope",
            "tenant_id",
            "scope_type",
            "scope_value",
            "status",
            "id",
        ),
        Index("ix_quality_slo_policies_updated", "tenant_id", "status", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_value: Mapped[str] = mapped_column(String(128))
    channel_id: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    active_scope_key: Mapped[Optional[str]] = mapped_column(String(192), default=None)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    certification_warning_minutes: Mapped[int] = mapped_column(Integer)
    certification_critical_minutes: Mapped[int] = mapped_column(Integer)
    waiver_warning_minutes: Mapped[int] = mapped_column(Integer)
    max_open_alerts: Mapped[int] = mapped_column(Integer)
    auto_queue_recertification: Mapped[bool] = mapped_column(Boolean)
    require_passing_certification: Mapped[bool] = mapped_column(Boolean)
    allow_active_waiver: Mapped[bool] = mapped_column(Boolean)
    policy_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    disabled_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    disabled_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class TenantReleaseQualityScanSchedule(Base):
    """Revision-fenced fixed-interval schedule for one active SLO policy."""

    __tablename__ = "tenant_release_quality_scan_schedules"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_quality_scan_schedules_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            "slo_policy_id",
            name="uq_quality_scan_schedules_scope_policy_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "active_policy_slot",
            name="uq_quality_scan_schedules_active_policy",
        ),
        CheckConstraint(
            "status IN ('active','paused','archived')", name="ck_quality_scan_schedules_status"
        ),
        CheckConstraint("revision > 0", name="ck_quality_scan_schedules_revision"),
        CheckConstraint(
            "interval_seconds BETWEEN 300 AND 604800", name="ck_quality_scan_schedules_interval"
        ),
        CheckConstraint(
            "(status='active' AND active_policy_slot=slo_policy_id AND paused_at IS NULL AND paused_by IS NULL AND archived_at IS NULL AND archived_by IS NULL) OR "
            "(status='paused' AND active_policy_slot IS NULL AND paused_at IS NOT NULL AND paused_by IS NOT NULL AND archived_at IS NULL AND archived_by IS NULL) OR "
            "(status='archived' AND active_policy_slot IS NULL AND archived_at IS NOT NULL AND archived_by IS NOT NULL)",
            name="ck_quality_scan_schedules_lifecycle",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_quality_scan_schedules_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_quality_scan_schedules_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "slo_policy_id"],
            [
                "tenant_release_quality_slo_policies.tenant_id",
                "tenant_release_quality_slo_policies.id",
            ],
            name="fk_quality_scan_schedules_policy",
        ),
        Index("ix_quality_scan_schedules_due", "status", "next_run_at", "id"),
        Index(
            "ix_quality_scan_schedules_policy",
            "tenant_id",
            "dataset_id",
            "slo_policy_id",
            "status",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    slo_policy_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    active_policy_slot: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    interval_seconds: Mapped[int] = mapped_column(Integer)
    next_run_at: Mapped[datetime] = mapped_column(_datetime6())
    last_enqueued_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    paused_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    paused_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    archived_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    archived_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class TenantReleaseQualityScanRun(Base):
    """Lease-fenced scan execution ledger for one deterministic schedule slot."""

    __tablename__ = "tenant_release_quality_scan_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_quality_scan_runs_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "schedule_id",
            "planned_at",
            name="uq_quality_scan_runs_schedule_planned",
        ),
        CheckConstraint(
            "status IN ('pending','claimed','running','completed','failed','cancelled')",
            name="ck_tenant_release_quality_scan_runs_status",
        ),
        CheckConstraint(
            "slo_policy_revision > 0 AND attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_tenant_release_quality_scan_runs_attempts",
        ),
        CheckConstraint(
            "observation_count >= 0 AND alert_count >= 0 AND recertification_job_count >= 0",
            name="ck_tenant_release_quality_scan_runs_counts",
        ),
        CheckConstraint(
            "(status IN ('pending','completed','failed','cancelled') AND claim_owner IS NULL AND claim_lease_until IS NULL) OR "
            "(status='claimed' AND claim_owner IS NOT NULL AND claim_lease_until IS NOT NULL AND heartbeat_at IS NULL) OR "
            "(status='running' AND claim_owner IS NOT NULL AND claim_lease_until IS NOT NULL AND heartbeat_at IS NOT NULL AND started_at IS NOT NULL)",
            name="ck_tenant_release_quality_scan_runs_execution_state",
        ),
        CheckConstraint(
            "(status IN ('pending','claimed','running') AND finished_at IS NULL) OR "
            "(status IN ('completed','failed','cancelled') AND finished_at IS NOT NULL AND claim_owner IS NULL AND claim_lease_until IS NULL)",
            name="ck_tenant_release_quality_scan_runs_terminal_state",
        ),
        CheckConstraint(
            "length(idempotency_key_digest)=64 AND lower(idempotency_key_digest)=idempotency_key_digest AND "
            "length(request_hash)=64 AND lower(request_hash)=request_hash AND "
            "(summary_digest IS NULL OR (length(summary_digest)=64 AND lower(summary_digest)=summary_digest))",
            name="ck_tenant_release_quality_scan_runs_digests",
        ),
        CheckConstraint(
            "(safe_error_code IS NULL AND safe_error IS NULL) OR (safe_error_code IS NOT NULL AND safe_error IS NOT NULL)",
            name="ck_tenant_release_quality_scan_runs_safe_error",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_quality_scan_runs_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_quality_scan_runs_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "schedule_id", "slo_policy_id"],
            [
                "tenant_release_quality_scan_schedules.tenant_id",
                "tenant_release_quality_scan_schedules.dataset_id",
                "tenant_release_quality_scan_schedules.id",
                "tenant_release_quality_scan_schedules.slo_policy_id",
            ],
            name="fk_quality_scan_runs_schedule_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "slo_policy_id"],
            [
                "tenant_release_quality_slo_policies.tenant_id",
                "tenant_release_quality_slo_policies.id",
            ],
            name="fk_quality_scan_runs_policy",
        ),
        Index("ix_quality_scan_runs_claim", "status", "next_attempt_at", "planned_at", "id"),
        Index(
            "ix_quality_scan_runs_schedule",
            "tenant_id",
            "dataset_id",
            "schedule_id",
            "planned_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    schedule_id: Mapped[str] = mapped_column(String(64))
    slo_policy_id: Mapped[str] = mapped_column(String(64))
    slo_policy_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    planned_at: Mapped[datetime] = mapped_column(_datetime6())
    claim_owner: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    claim_lease_until: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    started_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    finished_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    observation_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    alert_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    recertification_job_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    idempotency_key_digest: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    summary_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    safe_error_code: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    safe_error: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class DatasetReleaseQualityObservation(Base):
    """Append-only quality evidence for one Release/Channel scan authority."""

    __tablename__ = "dataset_release_quality_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_quality_observations_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "scan_run_id",
            "dataset_id",
            "release_id",
            "channel_id",
            "release_role",
            name="uq_quality_observations_run_authority",
        ),
        CheckConstraint(
            "release_role IN ('active','pinned')", name="ck_quality_observations_release_role"
        ),
        CheckConstraint(
            "severity IN ('healthy','warning','critical','unavailable')",
            name="ck_quality_observations_severity",
        ),
        CheckConstraint(
            "gate_state IN ('passing','waived','not_required','blocked','unavailable')",
            name="ck_quality_observations_gate_state",
        ),
        CheckConstraint("slo_policy_revision > 0", name="ck_quality_observations_policy_rev"),
        CheckConstraint(
            "length(observation_digest)=64 AND lower(observation_digest)=observation_digest",
            name="ck_quality_observations_digest",
        ),
        CheckConstraint(
            "(certification_id IS NULL AND certification_digest IS NULL AND certification_valid_until IS NULL AND minutes_to_certification_expiry IS NULL) OR "
            "(certification_id IS NOT NULL AND certification_digest IS NOT NULL AND certification_valid_until IS NOT NULL AND minutes_to_certification_expiry IS NOT NULL)",
            name="ck_quality_observations_certification",
        ),
        CheckConstraint(
            "(waiver_id IS NULL AND waiver_digest IS NULL AND waiver_expires_at IS NULL AND minutes_to_waiver_expiry IS NULL) OR "
            "(waiver_id IS NOT NULL AND waiver_digest IS NOT NULL AND waiver_expires_at IS NOT NULL AND minutes_to_waiver_expiry IS NOT NULL)",
            name="ck_quality_observations_waiver",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_quality_observations_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_quality_observations_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_quality_observations_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_quality_observations_channel",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "scan_run_id"],
            [
                "tenant_release_quality_scan_runs.tenant_id",
                "tenant_release_quality_scan_runs.dataset_id",
                "tenant_release_quality_scan_runs.id",
            ],
            name="fk_quality_observations_scan_run",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "slo_policy_id"],
            [
                "tenant_release_quality_slo_policies.tenant_id",
                "tenant_release_quality_slo_policies.id",
            ],
            name="fk_quality_observations_slo_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "certification_id"],
            [
                "dataset_release_quality_certifications.tenant_id",
                "dataset_release_quality_certifications.dataset_id",
                "dataset_release_quality_certifications.id",
            ],
            name="fk_quality_observations_certification",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "waiver_id"],
            [
                "dataset_release_quality_waivers.tenant_id",
                "dataset_release_quality_waivers.dataset_id",
                "dataset_release_quality_waivers.id",
            ],
            name="fk_quality_observations_waiver",
        ),
        Index("ix_quality_observations_scope_time", "tenant_id", "dataset_id", "observed_at", "id"),
        Index(
            "ix_quality_observations_severity",
            "tenant_id",
            "dataset_id",
            "severity",
            "observed_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    release_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(128))
    scan_run_id: Mapped[str] = mapped_column(String(64))
    slo_policy_id: Mapped[str] = mapped_column(String(64))
    slo_policy_revision: Mapped[int] = mapped_column(Integer)
    release_role: Mapped[str] = mapped_column(String(16))
    gate_state: Mapped[str] = mapped_column(String(24))
    gate_reason: Mapped[str] = mapped_column(String(512))
    certification_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    certification_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    certification_valid_until: Mapped[Optional[datetime]] = mapped_column(
        _datetime6(), default=None
    )
    waiver_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    waiver_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    waiver_expires_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    minutes_to_certification_expiry: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    minutes_to_waiver_expiry: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    severity: Mapped[str] = mapped_column(String(16))
    observation_digest: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(_datetime6())
    observed_by: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128))


class DatasetReleaseQualityAlert(Base):
    """Revision-fenced Alert Inbox projection backed by immutable observations."""

    __tablename__ = "dataset_release_quality_alerts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_quality_alerts_scope_id"),
        UniqueConstraint(
            "tenant_id", "active_alert_key", name="uq_dataset_release_quality_alerts_active_key"
        ),
        CheckConstraint("revision > 0 AND occurrence_count > 0", name="ck_quality_alerts_counts"),
        CheckConstraint(
            "release_role IN ('active','pinned')", name="ck_quality_alerts_release_role"
        ),
        CheckConstraint(
            "alert_type IN ('certification_expiring','certification_expired','certification_stale','waiver_expiring','waiver_expired','quality_gate_blocked','quality_authority_unavailable')",
            name="ck_quality_alerts_type",
        ),
        CheckConstraint("severity IN ('warning','critical')", name="ck_quality_alerts_severity"),
        CheckConstraint(
            "status IN ('open','acknowledged','resolved','suppressed')",
            name="ck_quality_alerts_status",
        ),
        CheckConstraint(
            _quality_operations_alert_active_key_check(), name="ck_quality_alerts_active_key"
        ),
        CheckConstraint(
            "(status='open' AND acknowledged_at IS NULL AND acknowledged_by IS NULL AND resolved_at IS NULL AND resolved_by IS NULL AND suppressed_until IS NULL AND suppressed_by IS NULL) OR "
            "(status='acknowledged' AND acknowledged_at IS NOT NULL AND acknowledged_by IS NOT NULL AND resolved_at IS NULL AND resolved_by IS NULL AND suppressed_until IS NULL AND suppressed_by IS NULL) OR "
            "(status='resolved' AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL AND suppressed_until IS NULL AND suppressed_by IS NULL) OR "
            "(status='suppressed' AND resolved_at IS NULL AND resolved_by IS NULL AND suppressed_until IS NOT NULL AND suppressed_by IS NOT NULL)",
            name="ck_dataset_release_quality_alerts_lifecycle",
        ),
        CheckConstraint(
            "length(source_observation_digest)=64 AND lower(source_observation_digest)=source_observation_digest",
            name="ck_quality_alerts_source_digest",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_quality_alerts_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_quality_alerts_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_quality_alerts_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_quality_alerts_channel",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "source_observation_id"],
            [
                "dataset_release_quality_observations.tenant_id",
                "dataset_release_quality_observations.dataset_id",
                "dataset_release_quality_observations.id",
            ],
            name="fk_quality_alerts_observation",
        ),
        Index(
            "ix_quality_alerts_inbox",
            "tenant_id",
            "dataset_id",
            "status",
            "severity",
            "last_observed_at",
            "id",
        ),
        Index(
            "ix_quality_alerts_authority",
            "tenant_id",
            "dataset_id",
            "release_id",
            "channel_id",
            "release_role",
            "alert_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    release_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(128))
    release_role: Mapped[str] = mapped_column(String(16))
    alert_type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    active_alert_key: Mapped[Optional[str]] = mapped_column(String(384), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    source_observation_id: Mapped[str] = mapped_column(String(64))
    source_observation_digest: Mapped[str] = mapped_column(String(64))
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    opened_at: Mapped[datetime] = mapped_column(_datetime6())
    last_observed_at: Mapped[datetime] = mapped_column(_datetime6())
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    acknowledged_comment: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    resolved_comment: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    suppressed_until: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    suppressed_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    suppressed_comment: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class DatasetReleaseRecertificationJob(Base):
    """Durable operator-driven recertification queue that delegates to Stage 20."""

    __tablename__ = "dataset_release_recertification_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_recertification_jobs_scope_id"),
        UniqueConstraint(
            "tenant_id", "active_job_key", name="uq_dataset_release_recertification_jobs_active_key"
        ),
        UniqueConstraint("tenant_id", "cycle_key", name="uq_recertification_jobs_cycle_key"),
        CheckConstraint(
            "release_role IN ('active','pinned')",
            name="ck_dataset_release_recertification_jobs_release_role",
        ),
        CheckConstraint(
            "trigger IN ('manual','certification_warning','certification_expired','stale_evidence','alert_escalation')",
            name="ck_dataset_release_recertification_jobs_trigger",
        ),
        CheckConstraint(
            "status IN ('pending','claimed','awaiting_evidence','ready_to_certify','completed','failed','cancelled')",
            name="ck_dataset_release_recertification_jobs_status",
        ),
        CheckConstraint(
            "policy_revision > 0 AND slo_policy_revision > 0 AND expected_channel_revision > 0 AND "
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_dataset_release_recertification_jobs_revisions",
        ),
        CheckConstraint(
            _quality_operations_job_active_key_check(),
            name="ck_dataset_release_recertification_jobs_active_key",
        ),
        CheckConstraint(
            "(status='claimed' AND claim_owner IS NOT NULL AND claim_lease_until IS NOT NULL) OR "
            "(status<>'claimed' AND claim_owner IS NULL AND claim_lease_until IS NULL AND heartbeat_at IS NULL)",
            name="ck_dataset_release_recertification_jobs_execution_state",
        ),
        CheckConstraint(
            "(status='completed' AND completed_at IS NOT NULL AND result_certification_id IS NOT NULL AND cancelled_at IS NULL AND cancelled_by IS NULL) OR "
            "(status='cancelled' AND completed_at IS NULL AND result_certification_id IS NULL AND cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL) OR "
            "(status NOT IN ('completed','cancelled') AND completed_at IS NULL AND result_certification_id IS NULL AND cancelled_at IS NULL AND cancelled_by IS NULL)",
            name="ck_dataset_release_recertification_jobs_terminal_state",
        ),
        CheckConstraint(
            "length(cycle_key)=64 AND lower(cycle_key)=cycle_key AND "
            "length(expected_manifest_digest)=64 AND lower(expected_manifest_digest)=expected_manifest_digest AND "
            "(expected_evidence_digest IS NULL OR (length(expected_evidence_digest)=64 AND lower(expected_evidence_digest)=expected_evidence_digest)) AND "
            "length(idempotency_key_digest)=64 AND lower(idempotency_key_digest)=idempotency_key_digest AND "
            "length(request_hash)=64 AND lower(request_hash)=request_hash",
            name="ck_dataset_release_recertification_jobs_digests",
        ),
        CheckConstraint(
            "(safe_error_code IS NULL AND safe_error IS NULL) OR (safe_error_code IS NOT NULL AND safe_error IS NOT NULL)",
            name="ck_dataset_release_recertification_jobs_safe_error",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_recertification_jobs_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_recertification_jobs_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_recertification_jobs_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_recertification_jobs_channel",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "baseline_id"],
            [
                "dataset_quality_baselines.tenant_id",
                "dataset_quality_baselines.dataset_id",
                "dataset_quality_baselines.id",
            ],
            name="fk_recertification_jobs_baseline",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "tenant_release_quality_gate_policies.tenant_id",
                "tenant_release_quality_gate_policies.id",
            ],
            name="fk_recertification_jobs_gate_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "slo_policy_id"],
            [
                "tenant_release_quality_slo_policies.tenant_id",
                "tenant_release_quality_slo_policies.id",
            ],
            name="fk_recertification_jobs_slo_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "result_certification_id"],
            [
                "dataset_release_quality_certifications.tenant_id",
                "dataset_release_quality_certifications.dataset_id",
                "dataset_release_quality_certifications.id",
            ],
            name="fk_recertification_jobs_result_cert",
        ),
        Index(
            "ix_recertification_jobs_queue",
            "tenant_id",
            "status",
            "next_attempt_at",
            "created_at",
            "id",
        ),
        Index(
            "ix_recertification_jobs_authority",
            "tenant_id",
            "dataset_id",
            "release_id",
            "channel_id",
            "release_role",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    release_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(128))
    release_role: Mapped[str] = mapped_column(String(16))
    baseline_id: Mapped[str] = mapped_column(String(64))
    policy_id: Mapped[str] = mapped_column(String(64))
    policy_revision: Mapped[int] = mapped_column(Integer)
    slo_policy_id: Mapped[str] = mapped_column(String(64))
    slo_policy_revision: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    active_job_key: Mapped[Optional[str]] = mapped_column(String(384), default=None)
    cycle_key: Mapped[str] = mapped_column(String(64))
    expected_manifest_digest: Mapped[str] = mapped_column(String(64))
    expected_evidence_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    expected_channel_revision: Mapped[int] = mapped_column(Integer)
    claim_owner: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    claim_lease_until: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    result_certification_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    idempotency_key_digest: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    safe_error_code: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    safe_error: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    cancelled_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    request_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(512))


class TenantNotificationSubscription(Base):
    """Per-account in-app notification preference for one Tenant category."""

    __tablename__ = "tenant_notification_subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_notification_subscriptions_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "active_subscription_key",
            name="uq_notification_subscriptions_active_key",
        ),
        CheckConstraint(
            "category IN ('quality','approval')",
            name="ck_notification_subscriptions_category",
        ),
        CheckConstraint(
            "status IN ('active','archived')",
            name="ck_notification_subscriptions_status",
        ),
        CheckConstraint(
            "preference IN ('subscribed','muted')",
            name="ck_notification_subscriptions_preference",
        ),
        CheckConstraint(
            "minimum_severity IN ('info','warning','critical')",
            name="ck_notification_subscriptions_severity",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_notification_subscriptions_revision",
        ),
        CheckConstraint(
            _notification_subscription_active_key_check(),
            name="ck_notification_subscriptions_active_key",
        ),
        CheckConstraint(
            "(preference='subscribed' AND muted_until IS NULL) OR preference='muted'",
            name="ck_notification_subscriptions_mute",
        ),
        CheckConstraint(
            "(status='active' AND active_subscription_key IS NOT NULL AND "
            "archived_at IS NULL AND archived_by IS NULL) OR "
            "(status='archived' AND active_subscription_key IS NULL AND "
            "archived_at IS NOT NULL AND archived_by IS NOT NULL)",
            name="ck_notification_subscriptions_lifecycle",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_notification_subscriptions_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_notification_subscriptions_member",
        ),
        Index(
            "ix_notification_subscriptions_account",
            "tenant_id",
            "account_id",
            "status",
            "category",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    preference: Mapped[str] = mapped_column(
        String(16), default="subscribed", server_default="subscribed"
    )
    active_subscription_key: Mapped[Optional[str]] = mapped_column(String(96), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    minimum_severity: Mapped[str] = mapped_column(
        String(16), default="warning", server_default="warning"
    )
    muted_until: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    archived_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    archived_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class TenantNotification(Base):
    """Immutable, body-free notification envelope."""

    __tablename__ = "tenant_notifications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_notifications_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "notification_key",
            name="uq_tenant_notifications_notification_key",
        ),
        CheckConstraint(
            "source_kind IN ('quality_alert','approval_pending_for_me')",
            name="ck_tenant_notifications_source_kind",
        ),
        CheckConstraint(
            "category IN ('quality','approval')",
            name="ck_tenant_notifications_category",
        ),
        CheckConstraint(
            "severity IN ('info','warning','critical')",
            name="ck_tenant_notifications_severity",
        ),
        CheckConstraint(
            "source_revision > 0",
            name="ck_tenant_notifications_source_revision",
        ),
        CheckConstraint(
            "action_required IN (false,true) AND mandatory IN (false,true)",
            name="ck_tenant_notifications_flags",
        ),
        CheckConstraint(
            "(source_kind='quality_alert' AND source_dataset_id IS NOT NULL AND category='quality') OR "
            "(source_kind='approval_pending_for_me' AND source_dataset_id IS NULL AND category='approval')",
            name="ck_tenant_notifications_source_scope",
        ),
        CheckConstraint(
            "(target_route_code='knowledge_quality_operations' AND source_kind='quality_alert') OR "
            "(target_route_code='enterprise_approval' AND source_kind='approval_pending_for_me')",
            name="ck_tenant_notifications_route",
        ),
        CheckConstraint(
            "length(notification_key)=64 AND lower(notification_key)=notification_key AND "
            "length(source_digest)=64 AND lower(source_digest)=source_digest",
            name="ck_tenant_notifications_digests",
        ),
        CheckConstraint(
            "length(title_code) BETWEEN 1 AND 64 AND length(summary_code) BETWEEN 1 AND 64",
            name="ck_tenant_notifications_codes",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_notifications_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_notifications_dataset",
        ),
        Index(
            "ix_tenant_notifications_source",
            "tenant_id",
            "source_kind",
            "source_id",
            "source_revision",
            "id",
        ),
        Index(
            "ix_tenant_notifications_category_time",
            "tenant_id",
            "category",
            "severity",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(128))
    source_revision: Mapped[int] = mapped_column(Integer)
    source_dataset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    category: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    action_required: Mapped[bool] = mapped_column(Boolean)
    mandatory: Mapped[bool] = mapped_column(Boolean)
    notification_key: Mapped[str] = mapped_column(String(64))
    source_digest: Mapped[str] = mapped_column(String(64))
    title_code: Mapped[str] = mapped_column(String(64))
    summary_code: Mapped[str] = mapped_column(String(64))
    safe_facts_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    target_route_code: Mapped[str] = mapped_column(String(48))
    target_route_params_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(_datetime6())
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))


class TenantNotificationRecipient(Base):
    """Immutable evidence for one Notification recipient assignment."""

    __tablename__ = "tenant_notification_recipients"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_notification_recipients_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "notification_id",
            "account_id",
            name="uq_notification_recipients_identity",
        ),
        CheckConstraint(
            "recipient_reason IN ('tenant_owner','tenant_admin','dataset_owner','eligible_approver','explicit_subscription')",
            name="ck_notification_recipients_reason",
        ),
        CheckConstraint(
            "mandatory IN (false,true)",
            name="ck_notification_recipients_mandatory",
        ),
        CheckConstraint(
            "length(assignment_digest)=64 AND lower(assignment_digest)=assignment_digest",
            name="ck_notification_recipients_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_notification_recipients_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "notification_id"],
            ["tenant_notifications.tenant_id", "tenant_notifications.id"],
            name="fk_notification_recipients_notification",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_notification_recipients_member",
        ),
        Index(
            "ix_notification_recipients_account",
            "tenant_id",
            "account_id",
            "assigned_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    notification_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    recipient_reason: Mapped[str] = mapped_column(String(32))
    mandatory: Mapped[bool] = mapped_column(Boolean)
    assignment_digest: Mapped[str] = mapped_column(String(64))
    assigned_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class TenantNotificationReceipt(Base):
    """Mutable per-account inbox receipt and unread-count authority."""

    __tablename__ = "tenant_notification_receipts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_notification_receipts_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "notification_id",
            "account_id",
            name="uq_notification_receipts_identity",
        ),
        CheckConstraint(
            "status IN ('unread','read','archived')",
            name="ck_notification_receipts_status",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_notification_receipts_revision",
        ),
        CheckConstraint(
            "(status='unread' AND read_at IS NULL AND archived_at IS NULL) OR "
            "(status='read' AND read_at IS NOT NULL AND archived_at IS NULL) OR "
            "(status='archived' AND archived_at IS NOT NULL)",
            name="ck_notification_receipts_lifecycle",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_notification_receipts_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "notification_id", "account_id"],
            [
                "tenant_notification_recipients.tenant_id",
                "tenant_notification_recipients.notification_id",
                "tenant_notification_recipients.account_id",
            ],
            name="fk_notification_receipts_recipient",
        ),
        Index(
            "ix_notification_receipts_inbox",
            "tenant_id",
            "account_id",
            "status",
            "updated_at",
            "notification_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    notification_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="unread", server_default="unread")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    read_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    archived_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class TenantNotificationEvent(Base):
    """Immutable receipt/materialization event hash chain."""

    __tablename__ = "tenant_notification_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_notification_events_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "notification_id",
            "account_id",
            "sequence",
            name="uq_notification_events_stream_sequence",
        ),
        CheckConstraint(
            "sequence > 0",
            name="ck_notification_events_sequence",
        ),
        CheckConstraint(
            "event_type IN ('materialized','marked_read','marked_unread','archived')",
            name="ck_notification_events_type",
        ),
        CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest AND "
            "(previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND lower(previous_event_digest)=previous_event_digest))",
            name="ck_notification_events_digests",
        ),
        CheckConstraint(
            "(sequence=1 AND previous_event_digest IS NULL) OR "
            "(sequence>1 AND previous_event_digest IS NOT NULL)",
            name="ck_notification_events_hash_chain",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_notification_events_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "notification_id", "account_id"],
            [
                "tenant_notification_recipients.tenant_id",
                "tenant_notification_recipients.notification_id",
                "tenant_notification_recipients.account_id",
            ],
            name="fk_notification_events_recipient",
        ),
        Index(
            "ix_notification_events_stream",
            "tenant_id",
            "notification_id",
            "account_id",
            "sequence",
            "id",
        ),
        Index(
            "ix_notification_events_time",
            "tenant_id",
            "account_id",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    notification_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(24))
    previous_event_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    event_digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128))
    safe_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class TenantContentRetentionPolicy(Base):
    """Tenant singleton governing the recoverable content retention boundary."""

    __tablename__ = "tenant_content_retention_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_content_retention_policies_tenant"),
        UniqueConstraint("tenant_id", "id", name="uq_tenant_content_retention_policies_scope_id"),
        CheckConstraint(
            "status IN ('active','paused')",
            name="ck_tenant_content_retention_policies_status",
        ),
        CheckConstraint(
            "retention_days BETWEEN 1 AND 3650",
            name="ck_tenant_content_retention_policies_retention_days",
        ),
        CheckConstraint(
            "auto_purge_enabled IN (false,true) AND purge_requires_approval IN (false,true)",
            name="ck_tenant_content_retention_policies_boolean_flags",
        ),
        CheckConstraint(
            "revision > 0", name="ck_tenant_content_retention_policies_revision_positive"
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_content_retention_policies_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_content_retention_policies_creator",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_content_retention_policies_updater",
        ),
        Index(
            "ix_tenant_content_retention_policies_tenant_status",
            "tenant_id",
            "status",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="'active'")
    retention_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    auto_purge_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    purge_requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64))


class TenantDocumentRecycleEntry(Base):
    """Current Tenant/Dataset/Document recycle authority and safe snapshot."""

    __tablename__ = "tenant_document_recycle_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_document_recycle_entries_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "document_id",
            "recycle_generation",
            name="uq_tenant_document_recycle_entries_generation",
        ),
        UniqueConstraint(
            "tenant_id", "active_recycle_key", name="uq_tenant_document_recycle_entries_active_key"
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            name="uq_tenant_document_recycle_entries_scope_entry",
        ),
        CheckConstraint(
            "status IN ('recycled','restoring','restored','purge_requested','purged','failed')",
            name="ck_tenant_document_recycle_entries_status",
        ),
        CheckConstraint(
            "recycle_generation > 0", name="ck_tenant_document_recycle_entries_generation"
        ),
        CheckConstraint(
            "revision > 0", name="ck_tenant_document_recycle_entries_revision_positive"
        ),
        CheckConstraint(
            "document_mutation_generation >= 0",
            name="ck_tenant_document_recycle_entries_document_generation",
        ),
        CheckConstraint(
            "original_lifecycle_state IN ('active','expired')",
            name="ck_tenant_document_recycle_entries_original_lifecycle",
        ),
        CheckConstraint(
            "retention_days_snapshot BETWEEN 1 AND 3650",
            name="ck_tenant_document_recycle_entries_retention_snapshot",
        ),
        CheckConstraint(
            "length(snapshot_digest)=64 AND lower(snapshot_digest)=snapshot_digest",
            name="ck_tenant_document_recycle_entries_snapshot_digest",
        ),
        CheckConstraint(
            _content_recovery_recycle_active_key_check(),
            name="ck_tenant_document_recycle_entries_active_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_document_recycle_entries_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_document_recycle_entries_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_tenant_document_recycle_entries_scope_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recycled_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_recycle_entries_recycler",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "restored_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_recycle_entries_restorer",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "purged_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_recycle_entries_purger",
        ),
        Index(
            "ix_tenant_document_recycle_entries_tenant_status_eligible",
            "tenant_id",
            "status",
            "purge_eligible_at",
            "id",
        ),
        Index(
            "ix_tenant_document_recycle_entries_scope_document",
            "tenant_id",
            "dataset_id",
            "document_id",
            "recycle_generation",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str] = mapped_column(String(64))
    recycle_generation: Mapped[int] = mapped_column(BigInteger)
    active_recycle_key: Mapped[Optional[str]] = mapped_column(String(160), default=None)
    status: Mapped[str] = mapped_column(String(24), default="recycled", server_default="'recycled'")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    document_mutation_generation: Mapped[int] = mapped_column(BigInteger)
    original_lifecycle_state: Mapped[str] = mapped_column(String(16))
    original_retrieval_enabled: Mapped[bool] = mapped_column(Boolean)
    retention_days_snapshot: Mapped[int] = mapped_column(Integer)
    recycled_at: Mapped[datetime] = mapped_column(_datetime6())
    recycled_by: Mapped[str] = mapped_column(String(64))
    purge_eligible_at: Mapped[datetime] = mapped_column(_datetime6())
    restored_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    restored_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    purge_requested_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    purged_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    purged_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    safe_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    snapshot_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class TenantDocumentLegalHold(Base):
    """Tenant-scoped legal hold protecting a recycled document from purge."""

    __tablename__ = "tenant_document_legal_holds"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_document_legal_holds_scope_id"),
        UniqueConstraint(
            "tenant_id", "active_hold_key", name="uq_tenant_document_legal_holds_active_key"
        ),
        CheckConstraint(
            "status IN ('active','released')", name="ck_tenant_document_legal_holds_status"
        ),
        CheckConstraint("revision > 0", name="ck_tenant_document_legal_holds_revision_positive"),
        CheckConstraint(
            "length(reason_code) BETWEEN 1 AND 64",
            name="ck_tenant_document_legal_holds_reason_code",
        ),
        CheckConstraint(
            "length(safe_reason) BETWEEN 1 AND 500",
            name="ck_tenant_document_legal_holds_safe_reason",
        ),
        CheckConstraint(
            _content_recovery_hold_active_key_check(),
            name="ck_tenant_document_legal_holds_active_key",
        ),
        CheckConstraint(
            "status <> 'released' OR (released_at IS NOT NULL AND released_by IS NOT NULL)",
            name="ck_tenant_document_legal_holds_release_evidence",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_document_legal_holds_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_document_legal_holds_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_tenant_document_legal_holds_scope_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "recycle_entry_id"],
            [
                "tenant_document_recycle_entries.tenant_id",
                "tenant_document_recycle_entries.dataset_id",
                "tenant_document_recycle_entries.id",
            ],
            name="fk_tenant_document_legal_holds_scope_entry",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "held_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_legal_holds_holder",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "released_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_legal_holds_releaser",
        ),
        Index(
            "ix_tenant_document_legal_holds_tenant_entry_status",
            "tenant_id",
            "dataset_id",
            "recycle_entry_id",
            "status",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str] = mapped_column(String(64))
    recycle_entry_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="'active'")
    active_hold_key: Mapped[Optional[str]] = mapped_column(String(192), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    reason_code: Mapped[str] = mapped_column(String(64))
    safe_reason: Mapped[str] = mapped_column(String(500))
    held_at: Mapped[datetime] = mapped_column(_datetime6())
    held_by: Mapped[str] = mapped_column(String(64))
    released_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    released_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class TenantDocumentPurgeRequest(Base):
    """Approval-gated purge prerequisite; never a physical deletion executor."""

    __tablename__ = "tenant_document_purge_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_document_purge_requests_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "requested_by",
            "idempotency_key_digest",
            name="uq_tenant_document_purge_requests_requester_key",
        ),
        CheckConstraint(
            "status IN ('pending_approval','approved','cancelled','expired','executed')",
            name="ck_tenant_document_purge_requests_status",
        ),
        CheckConstraint("revision > 0", name="ck_tenant_document_purge_requests_revision_positive"),
        CheckConstraint(
            "expected_entry_revision > 0",
            name="ck_tenant_document_purge_requests_expected_entry_revision",
        ),
        CheckConstraint(
            "length(request_digest)=64 AND lower(request_digest)=request_digest",
            name="ck_tenant_document_purge_requests_request_digest",
        ),
        CheckConstraint(
            "length(idempotency_key_digest)=64 AND lower(idempotency_key_digest)=idempotency_key_digest",
            name="ck_tenant_document_purge_requests_idempotency_digest",
        ),
        CheckConstraint(
            "legal_hold_count_snapshot >= 0", name="ck_tenant_document_purge_requests_hold_count"
        ),
        CheckConstraint(
            "status <> 'approved' OR approved_at IS NOT NULL",
            name="ck_tenant_document_purge_requests_approved_evidence",
        ),
        CheckConstraint(
            "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL)",
            name="ck_tenant_document_purge_requests_cancelled_evidence",
        ),
        CheckConstraint(
            "status <> 'executed' OR executed_at IS NOT NULL",
            name="ck_tenant_document_purge_requests_executed_evidence",
        ),
        CheckConstraint(
            "approval_request_id IS NOT NULL",
            name="ck_tenant_document_purge_requests_approval_reference",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_document_purge_requests_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_document_purge_requests_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_tenant_document_purge_requests_scope_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "recycle_entry_id"],
            [
                "tenant_document_recycle_entries.tenant_id",
                "tenant_document_recycle_entries.dataset_id",
                "tenant_document_recycle_entries.id",
            ],
            name="fk_tenant_document_purge_requests_scope_entry",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            ["tenant_approval_requests.tenant_id", "tenant_approval_requests.id"],
            name="fk_tenant_document_purge_requests_approval",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_purge_requests_requester",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "cancelled_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_purge_requests_canceller",
        ),
        Index(
            "ix_tenant_document_purge_requests_tenant_entry_status",
            "tenant_id",
            "dataset_id",
            "recycle_entry_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_tenant_document_purge_requests_tenant_status_expiry",
            "tenant_id",
            "status",
            "expires_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str] = mapped_column(String(64))
    recycle_entry_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(24), default="pending_approval", server_default="'pending_approval'"
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    expected_entry_revision: Mapped[int] = mapped_column(Integer)
    request_digest: Mapped[str] = mapped_column(String(64))
    idempotency_key_digest: Mapped[str] = mapped_column(String(64))
    approval_request_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    retention_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    legal_hold_count_snapshot: Mapped[int] = mapped_column(Integer)
    requested_at: Mapped[datetime] = mapped_column(_datetime6())
    requested_by: Mapped[str] = mapped_column(String(64))
    approved_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    cancelled_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    expires_at: Mapped[datetime] = mapped_column(_datetime6())
    executed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class TenantDocumentRecoveryEvent(Base):
    """Immutable append-only recovery event hash chain."""

    __tablename__ = "tenant_document_recovery_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_document_recovery_events_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "document_id",
            "recycle_entry_id",
            "sequence",
            name="uq_tenant_document_recovery_events_stream_sequence",
        ),
        CheckConstraint("sequence > 0", name="ck_tenant_document_recovery_events_sequence"),
        CheckConstraint(
            "event_type IN ('recycled','restored','hold_applied','hold_released','purge_requested','purge_approved','purge_cancelled')",
            name="ck_tenant_document_recovery_events_type",
        ),
        CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest AND "
            "(previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND lower(previous_event_digest)=previous_event_digest))",
            name="ck_tenant_document_recovery_events_digests",
        ),
        CheckConstraint(
            "(sequence=1 AND previous_event_digest IS NULL) OR "
            "(sequence>1 AND previous_event_digest IS NOT NULL)",
            name="ck_tenant_document_recovery_events_hash_chain",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_document_recovery_events_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_document_recovery_events_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_tenant_document_recovery_events_scope_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "recycle_entry_id"],
            [
                "tenant_document_recycle_entries.tenant_id",
                "tenant_document_recycle_entries.dataset_id",
                "tenant_document_recycle_entries.id",
            ],
            name="fk_tenant_document_recovery_events_scope_entry",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_recovery_events_actor",
        ),
        Index(
            "ix_tenant_document_recovery_events_scope_stream",
            "tenant_id",
            "dataset_id",
            "document_id",
            "recycle_entry_id",
            "sequence",
            "id",
        ),
        Index(
            "ix_tenant_document_recovery_events_scope_time",
            "tenant_id",
            "dataset_id",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str] = mapped_column(String(64))
    recycle_entry_id: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(24))
    previous_event_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    event_digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128))
    safe_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class TenantTaskProjection(Base):
    """Verified current projection of one Tenant-scoped source task."""

    __tablename__ = "tenant_task_projections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_task_projections_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "source_kind",
            "source_id",
            name="uq_tenant_task_projections_source",
        ),
        CheckConstraint(
            "source_kind IN ('document_ingest','index_operation','source_sync','document_delete',"
            "'audit_export','release_quality_scan','release_recertification')",
            name="ck_tenant_task_projections_source_kind",
        ),
        CheckConstraint(
            "category IN ('documents','indexing','sources','compliance','quality')",
            name="ck_tenant_task_projections_category",
        ),
        CheckConstraint(
            "normalized_status IN ('queued','running','succeeded','failed','cancelled','blocked','unavailable')",
            name="ck_tenant_task_projections_status",
        ),
        CheckConstraint("source_revision > 0", name="ck_tenant_task_projections_source_revision"),
        CheckConstraint(
            "action_required IN (false,true)",
            name="ck_tenant_task_projections_action_required",
        ),
        CheckConstraint(
            "progress_percent IS NULL OR progress_percent BETWEEN 0 AND 100",
            name="ck_tenant_task_projections_progress",
        ),
        CheckConstraint(
            "attempt_number >= 0 AND max_attempts > 0 AND attempt_number <= max_attempts",
            name="ck_tenant_task_projections_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_until IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_tenant_task_projections_lease",
        ),
        CheckConstraint(
            "(safe_error_code IS NULL AND safe_error IS NULL) OR "
            "(safe_error_code IS NOT NULL AND safe_error IS NOT NULL)",
            name="ck_tenant_task_projections_error",
        ),
        CheckConstraint(
            "target_route_code IS NULL OR target_route_code IN ('documents','indexing','sources','compliance','quality')",
            name="ck_tenant_task_projections_route",
        ),
        CheckConstraint(
            "length(source_digest)=64 AND lower(source_digest)=source_digest AND "
            "length(projection_digest)=64 AND lower(projection_digest)=projection_digest",
            name="ck_tenant_task_projections_digest",
        ),
        CheckConstraint(
            "source_current IN (false,true)", name="ck_tenant_task_projections_current"
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_task_projections_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_task_projections_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_task_projections_scope_workspace",
        ),
        Index(
            "ix_tenant_task_projections_tenant_status_updated",
            "tenant_id",
            "normalized_status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_tenant_task_projections_tenant_attention_updated",
            "tenant_id",
            "action_required",
            "source_current",
            "updated_at",
            "id",
        ),
        Index(
            "ix_tenant_task_projections_source_scope_updated",
            "tenant_id",
            "source_kind",
            "source_current",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(128))
    source_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    source_digest: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    workspace_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    category: Mapped[str] = mapped_column(String(24))
    normalized_status: Mapped[str] = mapped_column(
        String(24), default="queued", server_default="'queued'"
    )
    action_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    progress_percent: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    attempt_number: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    lease_owner: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    lease_until: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    safe_error_code: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    safe_error: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    target_route_code: Mapped[Optional[str]] = mapped_column(String(24), default=None)
    target_route_params_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    source_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    projection_digest: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(_datetime6())
    started_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    finished_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class TenantTaskOperatorAction(Base):
    """Tenant-scoped, revision-fenced request for a supported task action."""

    __tablename__ = "tenant_task_operator_actions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_task_operator_actions_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "actor_id",
            "idempotency_key_digest",
            name="uq_tenant_task_operator_actions_idempotency",
        ),
        CheckConstraint(
            "action_type IN ('retry','cancel','acknowledge')",
            name="ck_tenant_task_operator_actions_type",
        ),
        CheckConstraint(
            "status IN ('requested','dispatched','applied','rejected','expired')",
            name="ck_tenant_task_operator_actions_status",
        ),
        CheckConstraint(
            "expected_source_revision > 0",
            name="ck_tenant_task_operator_actions_revision",
        ),
        CheckConstraint(
            "length(expected_source_digest)=64 AND lower(expected_source_digest)=expected_source_digest "
            "AND length(idempotency_key_digest)=64 AND lower(idempotency_key_digest)=idempotency_key_digest",
            name="ck_tenant_task_operator_actions_digest",
        ),
        CheckConstraint(
            "length(safe_reason) BETWEEN 1 AND 512",
            name="ck_tenant_task_operator_actions_reason",
        ),
        CheckConstraint(
            "(status='requested' AND dispatched_at IS NULL AND applied_at IS NULL AND rejected_at IS NULL) OR "
            "(status='dispatched' AND dispatched_at IS NOT NULL AND applied_at IS NULL AND rejected_at IS NULL) OR "
            "(status='applied' AND applied_at IS NOT NULL AND rejected_at IS NULL) OR "
            "(status='rejected' AND rejected_at IS NOT NULL AND applied_at IS NULL) OR "
            "(status='expired' AND expires_at IS NOT NULL AND applied_at IS NULL)",
            name="ck_tenant_task_operator_actions_lifecycle",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_task_operator_actions_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tenant_task_projections.tenant_id", "tenant_task_projections.id"],
            name="fk_tenant_task_operator_actions_task",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_operator_actions_actor",
        ),
        Index(
            "ix_tenant_task_operator_actions_tenant_status_requested",
            "tenant_id",
            "status",
            "requested_at",
            "id",
        ),
        Index(
            "ix_tenant_task_operator_actions_task_created",
            "tenant_id",
            "task_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(String(64))
    action_type: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(
        String(24), default="requested", server_default="'requested'"
    )
    expected_source_revision: Mapped[int] = mapped_column(Integer)
    expected_source_digest: Mapped[str] = mapped_column(String(64))
    idempotency_key_digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128))
    safe_reason: Mapped[str] = mapped_column(String(512))
    result_code: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    requested_at: Mapped[datetime] = mapped_column(_datetime6())
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    applied_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    expires_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class TenantTaskEvent(Base):
    """Immutable append-only Task Operations event hash chain."""

    __tablename__ = "tenant_task_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_task_events_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "task_id",
            "sequence",
            name="uq_tenant_task_events_stream_sequence",
        ),
        CheckConstraint("sequence > 0", name="ck_tenant_task_events_sequence"),
        CheckConstraint(
            "event_type IN ('materialized','status_changed','source_stale','action_requested',"
            "'action_applied','action_rejected','attention_acknowledged')",
            name="ck_tenant_task_events_type",
        ),
        CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest AND "
            "(previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND "
            "lower(previous_event_digest)=previous_event_digest))",
            name="ck_tenant_task_events_digests",
        ),
        CheckConstraint(
            "(sequence=1 AND previous_event_digest IS NULL) OR "
            "(sequence>1 AND previous_event_digest IS NOT NULL)",
            name="ck_tenant_task_events_hash_chain",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tenant_task_events_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tenant_task_projections.tenant_id", "tenant_task_projections.id"],
            name="fk_tenant_task_events_task",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_events_actor",
        ),
        Index(
            "ix_tenant_task_events_task_sequence",
            "tenant_id",
            "task_id",
            "sequence",
            "id",
        ),
        Index(
            "ix_tenant_task_events_tenant_time",
            "tenant_id",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(32))
    previous_event_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    event_digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128))
    safe_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class TenantTaskSavedView(Base):
    """Per-account saved filter view with a canonical active identity."""

    __tablename__ = "tenant_task_saved_views"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_task_saved_views_scope_id"),
        UniqueConstraint(
            "tenant_id", "active_view_key", name="uq_tenant_task_saved_views_active_key"
        ),
        CheckConstraint(
            "status IN ('active','archived')", name="ck_tenant_task_saved_views_status"
        ),
        CheckConstraint("revision > 0", name="ck_tenant_task_saved_views_revision"),
        CheckConstraint(
            "length(name) BETWEEN 1 AND 128 AND length(normalized_name) BETWEEN 1 AND 128",
            name="ck_tenant_task_saved_views_name",
        ),
        CheckConstraint(
            "filters_json IS NOT NULL AND length(filter_digest)=64 AND "
            "lower(filter_digest)=filter_digest",
            name="ck_tenant_task_saved_views_filters",
        ),
        CheckConstraint(
            _task_saved_view_active_key_check(),
            name="ck_tenant_task_saved_views_active_key",
        ),
        CheckConstraint(
            "(status='active' AND active_view_key IS NOT NULL AND archived_at IS NULL AND archived_by IS NULL) OR "
            "(status='archived' AND active_view_key IS NULL AND archived_at IS NOT NULL AND archived_by IS NOT NULL)",
            name="ck_tenant_task_saved_views_lifecycle",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_task_saved_views_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_saved_views_member",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_saved_views_creator",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_saved_views_updater",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "archived_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_saved_views_archiver",
        ),
        Index(
            "ix_tenant_task_saved_views_account_status",
            "tenant_id",
            "account_id",
            "status",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="'active'")
    active_view_key: Mapped[Optional[str]] = mapped_column(String(192), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    filters_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    filter_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    archived_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    archived_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class TenantTaskReconciliationRun(Base):
    """Read-model build evidence for one Tenant reconciliation attempt."""

    __tablename__ = "tenant_task_reconciliation_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_task_reconciliation_runs_scope_id"),
        CheckConstraint(
            "status IN ('running','completed','failed')",
            name="ck_tenant_task_reconciliation_runs_status",
        ),
        CheckConstraint(
            "source_kinds_json IS NOT NULL",
            name="ck_tenant_task_reconciliation_runs_source_kinds",
        ),
        CheckConstraint(
            "length(source_inventory_digest)=64 AND lower(source_inventory_digest)=source_inventory_digest",
            name="ck_tenant_task_reconciliation_runs_digest",
        ),
        CheckConstraint(
            "source_count >= 0 AND created_count >= 0 AND updated_count >= 0 AND "
            "stale_count >= 0 AND invalid_count >= 0",
            name="ck_tenant_task_reconciliation_runs_counts",
        ),
        CheckConstraint(
            "(status='running' AND completed_at IS NULL) OR "
            "(status IN ('completed','failed') AND completed_at IS NOT NULL)",
            name="ck_tenant_task_reconciliation_runs_lifecycle_state",
        ),
        CheckConstraint(
            "(safe_error_code IS NULL AND safe_error IS NULL) OR "
            "(safe_error_code IS NOT NULL AND safe_error IS NOT NULL)",
            name="ck_tenant_task_reconciliation_runs_lifecycle",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_task_reconciliation_runs_tenant"
        ),
        Index(
            "ix_tenant_task_reconciliation_runs_tenant_status_started",
            "tenant_id",
            "status",
            "started_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="running", server_default="'running'")
    source_kinds_json: Mapped[list[str]] = mapped_column(JSON)
    source_inventory_digest: Mapped[str] = mapped_column(String(64))
    source_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    stale_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    invalid_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    safe_error_code: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    safe_error: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class TenantAutomationRule(Base):
    """Current Tenant-scoped automation rule metadata."""

    __tablename__ = "tenant_automation_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_automation_rules_scope_id"),
        UniqueConstraint(
            "tenant_id", "active_rule_key", name="uq_tenant_automation_rules_active_key"
        ),
        CheckConstraint(
            "status IN ('draft','active','paused','archived')",
            name="ck_tenant_automation_rules_status",
        ),
        CheckConstraint("revision > 0", name="ck_tenant_automation_rules_revision"),
        CheckConstraint("priority BETWEEN 0 AND 1000", name="ck_tenant_automation_rules_priority"),
        CheckConstraint(
            "(status='active' AND active_rule_key=normalized_name AND current_revision_id IS NOT NULL) OR "
            "(status<>'active' AND active_rule_key IS NULL)",
            name="ck_tenant_automation_rules_active_identity",
        ),
        CheckConstraint(
            "(status='archived' AND archived_at IS NOT NULL AND archived_by IS NOT NULL) OR "
            "(status<>'archived' AND archived_at IS NULL AND archived_by IS NULL)",
            name="ck_tenant_automation_rules_lifecycle",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_automation_rules_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "current_revision_id"],
            ["tenant_automation_rule_revisions.tenant_id", "tenant_automation_rule_revisions.id"],
            name="fk_tenant_automation_rules_current_revision",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_automation_rules_scope_workspace",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_automation_rules_scope_dataset",
        ),
        Index(
            "ix_tenant_automation_rules_tenant_status_updated",
            "tenant_id",
            "status",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="'draft'")
    active_rule_key: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    current_revision_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    workspace_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    dataset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    archived_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    archived_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class TenantAutomationRuleRevision(Base):
    """Immutable canonical automation rule definition."""

    __tablename__ = "tenant_automation_rule_revisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_automation_rule_revisions_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "rule_id",
            "revision",
            name="uq_tenant_automation_rule_revisions_rule_revision",
        ),
        UniqueConstraint(
            "tenant_id",
            "rule_id",
            "definition_digest",
            name="uq_tenant_automation_rule_revisions_definition",
        ),
        CheckConstraint("revision > 0", name="ck_tenant_automation_rule_revisions_revision"),
        CheckConstraint(
            "trigger_code IN ('task_failed','task_source_stale','source_sync_failed',"
            "'release_quality_alert_opened','release_recertification_blocked','approval_request_terminal')",
            name="ck_tenant_automation_rule_revisions_trigger",
        ),
        CheckConstraint(
            "condition_code IN ('always','status_is','action_required','severity_at_least',"
            "'attempt_exhausted','source_is_stale')",
            name="ck_tenant_automation_rule_revisions_condition",
        ),
        CheckConstraint(
            "condition_params_json IS NOT NULL AND action_plan_json IS NOT NULL",
            name="ck_tenant_automation_rule_revisions_definition",
        ),
        CheckConstraint(
            "length(definition_digest)=64 AND lower(definition_digest)=definition_digest",
            name="ck_tenant_automation_rule_revisions_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_automation_rule_revisions_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["tenant_automation_rules.tenant_id", "tenant_automation_rules.id"],
            name="fk_tenant_automation_rule_revisions_rule",
        ),
        Index(
            "ix_tenant_automation_rule_revisions_rule_created",
            "tenant_id",
            "rule_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    trigger_code: Mapped[str] = mapped_column(String(48))
    condition_code: Mapped[str] = mapped_column(String(48))
    condition_params_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    action_plan_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    definition_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))


class TenantAutomationSourceCursor(Base):
    """Replay and lease fence for one Rule/source stream."""

    __tablename__ = "tenant_automation_source_cursors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_automation_source_cursors_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "rule_id",
            "source_kind",
            "source_stream_id",
            name="uq_tenant_automation_source_cursors_stream",
        ),
        CheckConstraint(
            "source_kind IN ('task_failed','task_source_stale','source_sync_failed',"
            "'release_quality_alert_opened','release_recertification_blocked','approval_request_terminal')",
            name="ck_tenant_automation_source_cursors_source_kind",
        ),
        CheckConstraint(
            "status IN ('idle','claimed','blocked')",
            name="ck_tenant_automation_source_cursors_status",
        ),
        CheckConstraint(
            "last_sequence >= 0 AND revision > 0",
            name="ck_tenant_automation_source_cursors_revision",
        ),
        CheckConstraint(
            "(last_sequence=0 AND last_event_digest IS NULL) OR "
            "(last_sequence>0 AND length(last_event_digest)=64 AND lower(last_event_digest)=last_event_digest)",
            name="ck_tenant_automation_source_cursors_digest",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_until IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_tenant_automation_source_cursors_lease",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_automation_source_cursors_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["tenant_automation_rules.tenant_id", "tenant_automation_rules.id"],
            name="fk_tenant_automation_source_cursors_rule",
        ),
        Index(
            "ix_tenant_automation_source_cursors_tenant_status_updated",
            "tenant_id",
            "status",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(48))
    source_stream_id: Mapped[str] = mapped_column(String(128))
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    last_event_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(16), default="idle", server_default="'idle'")
    lease_owner: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    lease_until: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class TenantAutomationRun(Base):
    """One deterministic automation evaluation attempt."""

    __tablename__ = "tenant_automation_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_automation_runs_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "rule_id",
            "trigger_event_digest",
            name="uq_tenant_automation_runs_trigger_replay",
        ),
        CheckConstraint(
            "status IN ('started','not_matched','requested','completed','failed','blocked')",
            name="ck_tenant_automation_runs_status",
        ),
        CheckConstraint(
            "condition_matched IN (false,true)", name="ck_tenant_automation_runs_condition"
        ),
        CheckConstraint(
            "action_count >= 0 AND requested_count >= 0 AND rejected_count >= 0 AND "
            "requested_count + rejected_count <= action_count",
            name="ck_tenant_automation_runs_counts",
        ),
        CheckConstraint(
            "length(trigger_event_digest)=64 AND lower(trigger_event_digest)=trigger_event_digest AND "
            "length(idempotency_digest)=64 AND lower(idempotency_digest)=idempotency_digest",
            name="ck_tenant_automation_runs_digest",
        ),
        CheckConstraint(
            "(status='started' AND completed_at IS NULL) OR "
            "(status<>'started' AND completed_at IS NOT NULL)",
            name="ck_tenant_automation_runs_lifecycle",
        ),
        CheckConstraint(
            "(safe_error_code IS NULL AND safe_error IS NULL) OR "
            "(safe_error_code IS NOT NULL AND safe_error IS NOT NULL)",
            name="ck_tenant_automation_runs_error",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_automation_runs_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["tenant_automation_rules.tenant_id", "tenant_automation_rules.id"],
            name="fk_tenant_automation_runs_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_revision_id"],
            ["tenant_automation_rule_revisions.tenant_id", "tenant_automation_rule_revisions.id"],
            name="fk_tenant_automation_runs_revision",
        ),
        Index(
            "ix_tenant_automation_runs_tenant_status_started",
            "tenant_id",
            "status",
            "started_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str] = mapped_column(String(64))
    rule_revision_id: Mapped[str] = mapped_column(String(64))
    trigger_event_id: Mapped[str] = mapped_column(String(128))
    trigger_event_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="started", server_default="'started'")
    condition_matched: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    action_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    requested_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    idempotency_digest: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(_datetime6())
    completed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    safe_error_code: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    safe_error: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class TenantAutomationActionRequest(Base):
    """Bounded automation output request; never arbitrary execution."""

    __tablename__ = "tenant_automation_action_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_automation_action_requests_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_index",
            name="uq_tenant_automation_action_requests_run_step",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key_digest",
            name="uq_tenant_automation_action_requests_idempotency",
        ),
        CheckConstraint(
            "action_code IN ('notify_operator','request_approval','open_task_attention','pause_rule')",
            name="ck_tenant_automation_action_requests_action",
        ),
        CheckConstraint(
            "status IN ('requested','dispatched','applied','rejected','expired')",
            name="ck_tenant_automation_action_requests_status",
        ),
        CheckConstraint("step_index >= 0", name="ck_tenant_automation_action_requests_step"),
        CheckConstraint(
            "safe_params_json IS NOT NULL", name="ck_tenant_automation_action_requests_params"
        ),
        CheckConstraint(
            "length(idempotency_key_digest)=64 AND lower(idempotency_key_digest)=idempotency_key_digest",
            name="ck_tenant_automation_action_requests_idempotency",
        ),
        CheckConstraint(
            "(target_revision IS NULL AND target_digest IS NULL) OR "
            "(target_revision > 0 AND length(target_digest)=64 AND lower(target_digest)=target_digest)",
            name="ck_tenant_automation_action_requests_target_fence",
        ),
        CheckConstraint(
            "(status='requested' AND dispatched_at IS NULL AND applied_at IS NULL AND rejected_at IS NULL) OR "
            "(status='dispatched' AND dispatched_at IS NOT NULL AND applied_at IS NULL AND rejected_at IS NULL) OR "
            "(status='applied' AND applied_at IS NOT NULL AND rejected_at IS NULL) OR "
            "(status='rejected' AND rejected_at IS NOT NULL AND applied_at IS NULL) OR "
            "(status='expired' AND applied_at IS NULL)",
            name="ck_tenant_automation_action_requests_lifecycle",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_automation_action_requests_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["tenant_automation_runs.tenant_id", "tenant_automation_runs.id"],
            name="fk_tenant_automation_action_requests_run",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["tenant_automation_rules.tenant_id", "tenant_automation_rules.id"],
            name="fk_tenant_automation_action_requests_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            ["tenant_approval_requests.tenant_id", "tenant_approval_requests.id"],
            name="fk_tenant_automation_action_requests_approval",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "notification_id"],
            ["tenant_notifications.tenant_id", "tenant_notifications.id"],
            name="fk_tenant_automation_action_requests_notification",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tenant_task_projections.tenant_id", "tenant_task_projections.id"],
            name="fk_tenant_automation_action_requests_task",
        ),
        Index(
            "ix_tenant_automation_action_requests_tenant_status_requested",
            "tenant_id",
            "status",
            "requested_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str] = mapped_column(String(64))
    step_index: Mapped[int] = mapped_column(Integer)
    action_code: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(
        String(24), default="requested", server_default="'requested'"
    )
    target_kind: Mapped[Optional[str]] = mapped_column(String(48), default=None)
    target_id: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    target_revision: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    target_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    idempotency_key_digest: Mapped[str] = mapped_column(String(64))
    safe_params_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    safe_reason: Mapped[str] = mapped_column(String(512))
    approval_request_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    notification_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    task_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    requested_at: Mapped[datetime] = mapped_column(_datetime6())
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    applied_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    expires_at: Mapped[datetime] = mapped_column(_datetime6())
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class TenantAutomationEvent(Base):
    """Immutable Automation Rule/Run event chain."""

    __tablename__ = "tenant_automation_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_automation_events_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "stream_key",
            "sequence",
            name="uq_tenant_automation_events_stream_sequence",
        ),
        CheckConstraint(
            "event_type IN ('rule_created','revision_created','revision_activated','rule_paused',"
            "'trigger_observed','condition_not_matched','run_started','action_requested',"
            "'action_rejected','run_completed','run_failed')",
            name="ck_tenant_automation_events_type",
        ),
        CheckConstraint("sequence > 0", name="ck_tenant_automation_events_sequence"),
        CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest AND "
            "(previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND "
            "lower(previous_event_digest)=previous_event_digest))",
            name="ck_tenant_automation_events_digest",
        ),
        CheckConstraint(
            "(sequence=1 AND previous_event_digest IS NULL) OR "
            "(sequence>1 AND previous_event_digest IS NOT NULL)",
            name="ck_tenant_automation_events_hash_chain",
        ),
        CheckConstraint(
            "safe_snapshot_json IS NOT NULL", name="ck_tenant_automation_events_snapshot"
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_automation_events_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["tenant_automation_rules.tenant_id", "tenant_automation_rules.id"],
            name="fk_tenant_automation_events_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["tenant_automation_runs.tenant_id", "tenant_automation_runs.id"],
            name="fk_tenant_automation_events_run",
        ),
        Index(
            "ix_tenant_automation_events_tenant_time",
            "tenant_id",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    stream_key: Mapped[str] = mapped_column(String(128))
    sequence: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(48))
    previous_event_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    event_digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64))
    safe_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(_datetime6())


class DatasetAclMutationRequest(Base):
    """Durable idempotency ledger for Dataset ACL mutations."""

    __tablename__ = "dataset_acl_mutation_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "actor_id",
            "idempotency_key",
            name="uq_dataset_acl_mutation_requests_actor_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_acl_mutation_requests_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["actor_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_dataset_acl_mutation_requests_scope_actor",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_dataset_acl_mutation_requests_status",
        ),
        CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_dataset_acl_mutation_requests_idempotency_key_length",
        ),
        Index(
            "ix_dataset_acl_mutation_requests_tenant_dataset_status_created",
            "tenant_id",
            "dataset_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_dataset_acl_mutation_requests_tenant_actor_key",
            "tenant_id",
            "actor_id",
            "idempotency_key",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    resource_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    response_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    http_status: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)


class Document(Base):
    """文档：状态机主体（文档级进度）。"""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "source_uri_hash",
            name="uq_documents_dataset_source_uri_hash",
        ),
        UniqueConstraint(
            "dataset_id",
            "source_id",
            "external_id",
            name="uq_documents_dataset_source_external",
        ),
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_documents_scope_id"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "folder_id"],
            [
                "knowledge_folders.tenant_id",
                "knowledge_folders.dataset_id",
                "knowledge_folders.id",
            ],
            name="fk_documents_scope_folder",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "active_delete_operation_id"],
            [
                "document_delete_operations.tenant_id",
                "document_delete_operations.dataset_id",
                "document_delete_operations.id",
            ],
            name="fk_documents_scope_active_delete_operation",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "id", "current_version_id"],
            [
                "document_versions.tenant_id",
                "document_versions.dataset_id",
                "document_versions.document_id",
                "document_versions.id",
            ],
            name="fk_documents_scope_current_version",
            use_alter=True,
        ),
        CheckConstraint(
            "lifecycle_state IN ('active', 'expired', 'recycled', 'delete_requested', "
            "'deleting', 'delete_failed', 'deleted')",
            name="ck_documents_lifecycle_state",
        ),
        CheckConstraint(
            "lifecycle_state = 'active' OR NOT retrieval_enabled",
            name="ck_documents_retrieval_lifecycle",
        ),
        Index(
            "ix_documents_scope_effective",
            "tenant_id",
            "dataset_id",
            "lifecycle_state",
            "retrieval_enabled",
            "effective_from",
            "expires_at",
        ),
        Index(
            "ix_documents_scope_lifecycle",
            "tenant_id",
            "dataset_id",
            "lifecycle_state",
            "id",
        ),
        Index(
            "ix_documents_scope_retrieval",
            "tenant_id",
            "dataset_id",
            "retrieval_enabled",
            "id",
        ),
        Index("ix_documents_active_delete_operation", "active_delete_operation_id"),
        # Ordinary B-tree indexes for tenant-scoped exact filters and stable
        # updated_at/id pagination. They are deliberately not full-text indexes:
        # arbitrary contains/search acceleration belongs to a future search
        # capability, not to this catalog migration.
        Index(
            "ix_documents_catalog_scope_updated",
            "tenant_id",
            "dataset_id",
            "updated_at",
            "id",
        ),
        Index(
            "ix_documents_catalog_scope_status_updated",
            "tenant_id",
            "dataset_id",
            "status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_documents_catalog_scope_doc_type_updated",
            "tenant_id",
            "dataset_id",
            "doc_type",
            "updated_at",
            "id",
        ),
        Index(
            "ix_documents_catalog_scope_folder_updated",
            "tenant_id",
            "dataset_id",
            "logical_folder_path",
            "updated_at",
            "id",
            # Keep the utf8mb4 key bounded on MySQL/MariaDB while retaining
            # the same logical filter columns on SQLite and other dialects.
            mysql_length={"logical_folder_path": 191},
        ),
        CheckConstraint(
            "mutation_generation >= 0",
            name="ck_documents_mutation_generation_nonnegative",
        ),
    )

    id: Mapped[str] = _pk()
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)  # 冗余：检索过滤直查
    name: Mapped[str] = mapped_column(String(256))
    file_path: Mapped[str] = mapped_column(String(1024), default="")
    file_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    source_uri: Mapped[Optional[str]] = mapped_column(String(1024), default=None)
    source_uri_hash: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    external_id: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    logical_folder_path: Mapped[Optional[str]] = mapped_column(String(1024), default=None)
    folder_id: Mapped[Optional[str]] = mapped_column(String(64), default=None, index=True)
    source_type: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    source_id: Mapped[Optional[str]] = mapped_column(String(64), default=None, index=True)
    doc_type: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="waiting", index=True)
    status_detail: Mapped[str] = mapped_column(String(512), default="")
    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    parser_meta: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, default=dict
    )  # 解析器/版面摘要
    content_revision: Mapped[int] = mapped_column(Integer, default=0)
    desired_index_revision: Mapped[int] = mapped_column(Integer, default=0)
    indexed_revision: Mapped[int] = mapped_column(Integer, default=0)
    graph_revision: Mapped[int] = mapped_column(Integer, default=0)
    current_attempt_id: Mapped[Optional[str]] = mapped_column(String(64), default=None, index=True)
    current_version_id: Mapped[Optional[str]] = mapped_column(String(64), default=None, index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(24), default="active", index=True)
    retrieval_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    effective_from: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    expires_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None, index=True)
    purge_after: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None, index=True)
    mutation_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    active_delete_operation_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    deletion_requested_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    usage_released_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    dataset: Mapped["Dataset"] = relationship(back_populates="documents")
    segments: Mapped[list["DocumentSegment"]] = relationship(back_populates="document")
    ingest_attempts: Mapped[list["DocumentIngestAttempt"]] = relationship(back_populates="document")


class DocumentVersion(Base):
    """Immutable authoritative source/parser snapshot for one document revision."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "document_id",
            "id",
            name="uq_document_versions_scope_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "document_id",
            "revision",
            name="uq_document_versions_document_revision",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_document_versions_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_document_versions_scope_document",
        ),
        CheckConstraint("revision > 0", name="ck_document_versions_revision_positive"),
        Index(
            "ix_document_versions_scope_revision",
            "tenant_id",
            "dataset_id",
            "document_id",
            "revision",
        ),
        Index(
            "ix_document_versions_scope_hash",
            "tenant_id",
            "dataset_id",
            "source_hash",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    source_identity: Mapped[str] = mapped_column(String(512), default="")
    source_hash: Mapped[str] = mapped_column(String(64), default="")
    parser_policy_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    parser_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    source_content_ref: Mapped[str] = mapped_column(String(1024), default="")
    created_by: Mapped[str] = mapped_column(String(64))
    change_reason: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class DocumentSegment(Base):
    """文档分段：分段级进度追踪（解析出的自然段 / 切分单元）。"""

    __tablename__ = "document_segments"
    __table_args__ = (
        UniqueConstraint("document_id", "seq", name="uq_document_segments_document_seq"),
    )

    id: Mapped[str] = _pk()
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    segment_meta: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _now()

    document: Mapped["Document"] = relationship(back_populates="segments")


class DocumentIngestAttempt(Base):
    """一次不可变编号的文档入库尝试。"""

    __tablename__ = "document_ingest_attempts"
    __table_args__ = (
        UniqueConstraint("document_id", "attempt_no", name="uq_ingest_attempt_document_no"),
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_ingest_attempt_scope_id"),
        Index("ix_ingest_attempt_state_started", "state", "started_at"),
        Index("ix_ingest_attempt_document_created", "document_id", "created_at"),
        Index(
            "ix_ingest_attempt_document_generation",
            "document_id",
            "document_generation",
            "attempt_kind",
        ),
        CheckConstraint(
            "attempt_kind IN ('ingest', 'reindex', 'source_sync', 'chunk_mutation', "
            "'document_delete', 'restore')",
            name="ck_ingest_attempt_kind",
        ),
        CheckConstraint(
            "document_generation >= 0",
            name="ck_ingest_attempt_document_generation_nonnegative",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    input_content_hash: Mapped[str] = mapped_column(String(64), default="")
    input_revision: Mapped[int] = mapped_column(Integer, default=0)
    attempt_kind: Mapped[str] = mapped_column(String(24), default="ingest")
    document_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[str] = mapped_column(String(24), default="running", index=True)
    primary_index_ready_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    pending_subtasks: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    worker_id: Mapped[str] = mapped_column(String(64), default="")
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None, index=True)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    document: Mapped["Document"] = relationship(back_populates="ingest_attempts")
    spans: Mapped[list["DocumentIngestSpan"]] = relationship(back_populates="attempt")


class DocumentIngestSpan(Base):
    """Attempt 内可回放的阶段/子阶段执行记录。"""

    __tablename__ = "document_ingest_spans"
    __table_args__ = (
        UniqueConstraint("attempt_id", "span_id", name="uq_ingest_span_attempt_span"),
        Index("ix_ingest_span_attempt_created", "attempt_id", "created_at"),
        Index("ix_ingest_span_status_started", "status", "started_at"),
        Index("ix_ingest_span_parent", "parent_span_id"),
    )

    id: Mapped[str] = _pk()
    attempt_id: Mapped[str] = mapped_column(ForeignKey("document_ingest_attempts.id"), index=True)
    span_id: Mapped[str] = mapped_column(String(64))
    parent_span_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24), default="stage")
    status: Mapped[str] = mapped_column(String(24), default="running")
    input: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    output: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    span_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    attempt: Mapped["DocumentIngestAttempt"] = relationship(back_populates="spans")


class IndexOperation(Base):
    """跨 MySQL/Milvus/图投影的持久化期望操作。"""

    __tablename__ = "index_operations"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_index_operations_dedup_key"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "delete_operation_id"],
            [
                "document_delete_operations.tenant_id",
                "document_delete_operations.dataset_id",
                "document_delete_operations.id",
            ],
            name="fk_index_operations_scope_delete_operation",
        ),
        Index("ix_index_operations_ready", "status", "next_retry_at", "created_at"),
        Index("ix_index_operations_document_revision", "document_id", "target_revision"),
        Index("ix_index_operations_lease", "status", "lease_until"),
        Index("ix_index_operations_delete_status", "delete_operation_id", "status"),
        Index(
            "ix_index_operations_document_generation",
            "document_id",
            "document_generation",
            "status",
        ),
        CheckConstraint(
            "document_generation >= 0",
            name="ck_index_operations_document_generation_nonnegative",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("document_ingest_attempts.id"), index=True)
    target_store: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(24))
    dedup_key: Mapped[str] = mapped_column(String(255))
    target_revision: Mapped[int] = mapped_column(Integer, default=0)
    document_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    delete_operation_id: Mapped[Optional[str]] = mapped_column(String(64), default=None, index=True)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    claimed_by: Mapped[str] = mapped_column(String(64), default="")
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None, index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None, index=True)
    last_error_code: Mapped[str] = mapped_column(String(64), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)


class IndexDeadLetter(Base):
    """超过重试预算的投影操作，支持人工审计和重放。"""

    __tablename__ = "index_dead_letters"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_index_dead_letters_operation"),
        Index("ix_index_dead_letters_document_failed", "document_id", "failed_at"),
        Index("ix_index_dead_letters_target_failed", "target_store", "failed_at"),
    )

    id: Mapped[str] = _pk()
    operation_id: Mapped[str] = mapped_column(ForeignKey("index_operations.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    attempt_id: Mapped[str] = mapped_column(String(64), index=True)
    dedup_key: Mapped[str] = mapped_column(String(255))
    target_store: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(24))
    target_revision: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str] = mapped_column(String(64), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    failed_at: Mapped[datetime] = _now()
    requeued_to_operation_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    operator_note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = _now()


class DataSourceRecord(Base):
    """外部数据源的稳定配置身份与最新同步游标。"""

    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_data_sources_tenant_name"),
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_data_sources_scope_id"),
        Index("ix_data_sources_dataset_status", "dataset_id", "status"),
        CheckConstraint(
            "mutation_generation >= 0",
            name="ck_data_sources_mutation_generation_nonnegative",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    source_type: Mapped[str] = mapped_column(String(64))
    effective_config: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    config_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    last_cursor: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    last_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    mutation_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SourceSchedule(Base):
    """Authoritative fixed-interval UTC schedule for one knowledge source."""

    __tablename__ = "source_schedules"
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_source_schedules_source"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "source_id",
            "id",
            name="uq_source_schedules_scope_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_source_schedules_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "source_id"],
            ["data_sources.tenant_id", "data_sources.dataset_id", "data_sources.id"],
            name="fk_source_schedules_scope_source",
        ),
        ForeignKeyConstraint(
            ["last_run_id"],
            ["source_sync_runs.id"],
            name="fk_source_schedules_last_run",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_source_schedules_revision_positive",
        ),
        CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name="ck_source_schedules_status",
        ),
        CheckConstraint(
            "interval_seconds >= 300 AND interval_seconds <= 604800",
            name="ck_source_schedules_interval",
        ),
        CheckConstraint(
            "force_full IN (0, 1)",
            name="ck_source_schedules_force_full",
        ),
        Index("ix_source_schedules_due", "status", "next_run_at", "id"),
        Index(
            "ix_source_schedules_status",
            "tenant_id",
            "dataset_id",
            "status",
            "id",
        ),
        Index(
            "ix_source_schedules_scope",
            "tenant_id",
            "dataset_id",
            "source_id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="active")
    interval_seconds: Mapped[int] = mapped_column(Integer)
    force_full: Mapped[bool] = mapped_column(Boolean, default=False)
    next_run_at: Mapped[datetime] = mapped_column(_datetime6())
    last_enqueued_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    last_run_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SourceSyncRun(Base):
    """一次可审计、可续跑的数据源同步。"""

    __tablename__ = "source_sync_runs"
    __table_args__ = (
        Index("ix_source_sync_runs_source_started", "source_id", "started_at"),
        Index("ix_source_sync_runs_status_started", "status", "started_at"),
        UniqueConstraint(
            "source_id",
            "source_generation",
            "dataset_generation",
            "idempotency_key",
            name="uq_source_sync_runs_idempotency",
        ),
        UniqueConstraint("retry_of_run_id", name="uq_source_sync_runs_retry_of"),
        ForeignKeyConstraint(
            ["retry_of_run_id"],
            ["source_sync_runs.id"],
            name="fk_source_sync_runs_retry_of",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "source_id", "schedule_id"],
            [
                "source_schedules.tenant_id",
                "source_schedules.dataset_id",
                "source_schedules.source_id",
                "source_schedules.id",
            ],
            name="fk_source_sync_runs_schedule",
        ),
        CheckConstraint(
            "source_generation >= 0 AND dataset_generation >= 0 AND pending_deletes >= 0",
            name="ck_source_sync_runs_generations_counts",
        ),
        CheckConstraint(
            "(idempotency_key IS NULL AND request_hash IS NULL) OR "
            "(idempotency_key IS NOT NULL AND request_hash IS NOT NULL)",
            name="ck_source_sync_runs_idempotency_pair",
        ),
        CheckConstraint(
            "retry_of_run_id IS NULL OR retry_of_run_id <> id",
            name="ck_source_sync_runs_retry_not_self",
        ),
        CheckConstraint(
            "(trigger = 'retry' AND retry_of_run_id IS NOT NULL) OR "
            "(trigger <> 'retry' AND retry_of_run_id IS NULL)",
            name="ck_source_sync_runs_retry_trigger",
        ),
        CheckConstraint(
            "schedule_revision IS NULL OR schedule_revision > 0",
            name="ck_source_sync_runs_schedule_revision",
        ),
        CheckConstraint(
            "(trigger = 'scheduled' AND schedule_id IS NOT NULL "
            "AND schedule_revision IS NOT NULL AND planned_at IS NOT NULL) OR "
            "(trigger <> 'scheduled' AND schedule_id IS NULL "
            "AND schedule_revision IS NULL AND planned_at IS NULL)",
            name="ck_source_sync_runs_schedule_metadata",
        ),
        Index(
            "ix_source_sync_runs_schedule_planned",
            "schedule_id",
            "schedule_revision",
            "planned_at",
        ),
        CheckConstraint(
            "execution_state IN ('pending', 'executing', 'failed', 'completed')",
            name="ck_source_sync_runs_execution_state",
        ),
        CheckConstraint(
            "execution_attempts >= 0",
            name="ck_source_sync_runs_execution_attempts",
        ),
        CheckConstraint(
            "(execution_state = 'executing' AND execution_owner <> '' "
            "AND execution_lease_until IS NOT NULL AND execution_heartbeat_at IS NOT NULL) OR "
            "(execution_state <> 'executing' AND execution_owner = '' "
            "AND execution_lease_until IS NULL AND execution_heartbeat_at IS NULL)",
            name="ck_source_sync_runs_execution_owner",
        ),
        CheckConstraint(
            "reservation_attempts >= 0",
            name="ck_source_sync_runs_reservation_attempts",
        ),
        CheckConstraint(
            "(reservation_owner = '' AND reservation_lease_until IS NULL) OR "
            "(reservation_owner <> '' AND reservation_lease_until IS NOT NULL)",
            name="ck_source_sync_runs_reservation_owner",
        ),
    )

    id: Mapped[str] = _pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    trigger: Mapped[str] = mapped_column(String(24), default="manual")
    force_full: Mapped[int] = mapped_column(Integer, default=0)
    dry_run: Mapped[int] = mapped_column(Integer, default=0)
    source_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    dataset_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(128).with_variant(mysql.VARCHAR(128, collation="utf8mb4_bin"), "mysql"),
        default=None,
    )
    request_hash: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    retry_of_run_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    schedule_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    schedule_revision: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    planned_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    execution_state: Mapped[str] = mapped_column(String(16), default="completed")
    execution_owner: Mapped[str] = mapped_column(String(128), default="")
    execution_lease_until: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    execution_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    execution_attempts: Mapped[int] = mapped_column(Integer, default=0)
    execution_last_error: Mapped[str] = mapped_column(Text, default="")
    execution_started_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    execution_finished_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    execution_next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        _datetime6(), default=None
    )
    reservation_owner: Mapped[str] = mapped_column(String(128), default="")
    reservation_lease_until: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    reservation_attempts: Mapped[int] = mapped_column(Integer, default=0)
    pending_deletes: Mapped[int] = mapped_column(Integer, default=0)
    cursor_before: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    cursor_after: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    ingested: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    removed: Mapped[int] = mapped_column(Integer, default=0)
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    fetch_error: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = _now()


class SourceSyncItem(Base):
    """同步 run 内一篇外部文档的动作与结果。"""

    __tablename__ = "source_sync_items"
    __table_args__ = (
        UniqueConstraint("run_id", "external_id", name="uq_source_sync_items_run_external"),
        Index("ix_source_sync_items_source_result", "source_id", "result"),
    )

    id: Mapped[str] = _pk()
    run_id: Mapped[str] = mapped_column(ForeignKey("source_sync_runs.id"), index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(512))
    doc_id: Mapped[str] = mapped_column(String(128), index=True)
    source_uri: Mapped[str] = mapped_column(String(1024), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(24))
    result: Mapped[str] = mapped_column(String(24), index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SourceDocumentState(Base):
    """数据源每篇文档的当前增量同步状态。"""

    __tablename__ = "source_document_states"
    __table_args__ = (
        UniqueConstraint("source_id", "doc_id", name="uq_source_document_state_doc"),
        ForeignKeyConstraint(
            ["doc_id", "delete_operation_id"],
            ["document_delete_operations.document_id", "document_delete_operations.id"],
            name="fk_source_document_states_scope_delete_operation",
        ),
        Index("ix_source_document_state_source_updated", "source_id", "updated_at"),
        Index("ix_source_document_state_delete_operation", "delete_operation_id"),
        CheckConstraint(
            "state IN ('active', 'operator_suppressed', 'upstream_absent', 'delete_pending')",
            name="ck_source_document_states_state",
        ),
        CheckConstraint(
            "document_generation >= 0",
            name="ck_source_document_states_document_generation_nonnegative",
        ),
    )

    id: Mapped[str] = _pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id"), index=True)
    doc_id: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(512))
    source_uri: Mapped[str] = mapped_column(String(1024), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_id: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(24), default="active")
    document_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    delete_operation_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    suppressed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class DocumentDeleteBatch(Base):
    """Durable idempotent parent request for one or many document deletions."""

    __tablename__ = "document_delete_batches"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            name="uq_document_delete_batches_scope_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "idempotency_key",
            name="uq_document_delete_batches_idempotency",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_document_delete_batches_scope_dataset",
        ),
        CheckConstraint(
            "status IN ('preparing', 'running', 'completed', 'partially_failed', 'failed')",
            name="ck_document_delete_batches_status",
        ),
        CheckConstraint(
            "requested_count >= 0 AND accepted_count >= 0 AND completed_count >= 0 "
            "AND failed_count >= 0 AND rejected_count >= 0",
            name="ck_document_delete_batches_counts",
        ),
        Index(
            "ix_document_delete_batches_scope_status",
            "tenant_id",
            "dataset_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128), default="")
    reason: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(24), default="preparing", index=True)
    requested_count: Mapped[int] = mapped_column(Integer)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)


class DocumentDeleteOperation(Base):
    """Durable desired deletion state for one requested document."""

    __tablename__ = "document_delete_operations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            name="uq_document_delete_operations_scope_id",
        ),
        UniqueConstraint(
            "document_id",
            "delete_generation",
            name="uq_document_delete_operations_generation",
        ),
        UniqueConstraint(
            "document_id",
            "id",
            name="uq_document_delete_operations_document_id",
        ),
        UniqueConstraint(
            "batch_id",
            "request_index",
            name="uq_document_delete_operations_batch_index",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_document_delete_operations_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "batch_id"],
            [
                "document_delete_batches.tenant_id",
                "document_delete_batches.dataset_id",
                "document_delete_batches.id",
            ],
            name="fk_document_delete_operations_scope_batch",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_document_delete_operations_scope_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "attempt_id"],
            [
                "document_ingest_attempts.tenant_id",
                "document_ingest_attempts.dataset_id",
                "document_ingest_attempts.id",
            ],
            name="fk_document_delete_operations_scope_attempt",
        ),
        CheckConstraint(
            "origin IN ('operator', 'source_sync', 'dataset_reset', 'retention')",
            name="ck_document_delete_operations_origin",
        ),
        CheckConstraint(
            "status IN ('rejected', 'queued', 'projecting', 'finalizing', 'completed', 'failed')",
            name="ck_document_delete_operations_status",
        ),
        CheckConstraint(
            "request_index >= 0 AND chunk_manifest_count >= 0 AND quota_chunk_count >= 0 "
            "AND required_store_count >= 0 AND completed_store_count >= 0 "
            "AND failed_store_count >= 0 AND completed_store_count + failed_store_count <= required_store_count",
            name="ck_document_delete_operations_counts",
        ),
        CheckConstraint(
            "(expected_generation IS NULL OR expected_generation >= 0) AND "
            "(delete_generation IS NULL OR delete_generation >= 0)",
            name="ck_document_delete_operations_generations",
        ),
        Index("ix_document_delete_operations_batch_status", "batch_id", "status"),
        Index("ix_document_delete_operations_document_status", "document_id", "status"),
        Index(
            "ix_document_delete_operations_scope_status",
            "tenant_id",
            "dataset_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = _pk()
    batch_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    request_index: Mapped[int] = mapped_column(Integer, default=0)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    requested_document_id: Mapped[str] = mapped_column(String(128))
    document_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    expected_generation: Mapped[Optional[int]] = mapped_column(BigInteger, default=None)
    delete_generation: Mapped[Optional[int]] = mapped_column(BigInteger, default=None)
    attempt_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    origin: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), index=True)
    result_code: Mapped[str] = mapped_column(String(64), default="")
    result_message: Mapped[str] = mapped_column(Text, default="")
    chunk_manifest_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_manifest_hash: Mapped[str] = mapped_column(String(64), default="")
    quota_chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    required_store_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_store_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_store_count: Mapped[int] = mapped_column(Integer, default=0)
    requested_by: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128), default="")
    reason: Mapped[str] = mapped_column(String(512), default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    finalized_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ChunkHead(Base):
    """Chunk 当前权威内容；Milvus/图存储是其 revision 投影。"""

    __tablename__ = "chunk_heads"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "document_revision",
            "chunk_index",
            name="uq_chunk_heads_document_revision_index",
        ),
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_chunk_heads_scope_id"),
        Index("ix_chunk_heads_projection", "document_id", "document_revision", "enabled"),
        Index("ix_chunk_heads_parent", "parent_chunk_id"),
    )

    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    parent_chunk_id: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    chunk_index: Mapped[int] = mapped_column(Integer)
    chunk_role: Mapped[str] = mapped_column(String(16), default="flat")
    document_revision: Mapped[int] = mapped_column(Integer, default=0)
    content_revision: Mapped[int] = mapped_column(Integer, default=0)
    source_content: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    desired_index_revision: Mapped[int] = mapped_column(Integer, default=0)
    indexed_revision: Mapped[int] = mapped_column(Integer, default=0)
    index_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    editor_id: Mapped[str] = mapped_column(String(64), default="")
    edit_source: Mapped[str] = mapped_column(String(24), default="pipeline")
    context_header: Mapped[str] = mapped_column(Text, default="")
    chunk_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ChunkRevision(Base):
    """被新 head 取代的不可变 Chunk 内容快照。"""

    __tablename__ = "chunk_revisions"
    __table_args__ = (
        UniqueConstraint("chunk_id", "revision", name="uq_chunk_revisions_chunk_revision"),
        Index("ix_chunk_revisions_chunk_edited", "chunk_id", "edited_at"),
    )

    id: Mapped[str] = _pk()
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunk_heads.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    editor_id: Mapped[str] = mapped_column(String(64), default="")
    edit_source: Mapped[str] = mapped_column(String(24), default="user")
    edited_at: Mapped[datetime] = _now()
    created_at: Mapped[datetime] = _now()


class KnowledgeFolder(Base):
    """Tenant/dataset-scoped authoritative knowledge folder hierarchy."""

    __tablename__ = "knowledge_folders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_knowledge_folders_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "parent_key",
            "normalized_name",
            name="uq_knowledge_folders_scope_sibling_name",
        ),
        CheckConstraint(
            "parent_key = COALESCE(parent_id, '')",
            name="ck_knowledge_folders_parent_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_knowledge_folders_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "parent_id"],
            [
                "knowledge_folders.tenant_id",
                "knowledge_folders.dataset_id",
                "knowledge_folders.id",
            ],
            name="fk_knowledge_folders_scope_parent",
        ),
        Index(
            "ix_knowledge_folders_scope_parent_sort",
            "tenant_id",
            "dataset_id",
            "parent_key",
            "sort_order",
        ),
        Index(
            "ix_knowledge_folders_scope_path_hash",
            "tenant_id",
            "dataset_id",
            "path_hash",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String(64), default=None, index=True)
    parent_key: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(256))
    path: Mapped[str] = mapped_column(String(2048))
    path_hash: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class KnowledgeTag(Base):
    """Dataset-scoped governance tag with normalized unique identity."""

    __tablename__ = "knowledge_tags"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_knowledge_tags_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "normalized_name",
            name="uq_knowledge_tags_scope_name",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_knowledge_tags_scope_dataset",
        ),
        Index(
            "ix_knowledge_tags_scope_name",
            "tenant_id",
            "dataset_id",
            "normalized_name",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(256))
    color: Mapped[str] = mapped_column(String(32), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class DocumentTag(Base):
    """Authoritative document-to-governance-tag association."""

    __tablename__ = "document_tags"
    __table_args__ = (
        UniqueConstraint("document_id", "tag_id", name="uq_document_tags_document_tag"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_document_tags_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_document_tags_scope_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "tag_id"],
            ["knowledge_tags.tenant_id", "knowledge_tags.dataset_id", "knowledge_tags.id"],
            name="fk_document_tags_scope_tag",
        ),
        Index(
            "ix_document_tags_scope_document",
            "tenant_id",
            "dataset_id",
            "document_id",
        ),
        Index(
            "ix_document_tags_scope_tag",
            "tenant_id",
            "dataset_id",
            "tag_id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    tag_id: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = _now()


class QAKnowledge(Base):
    """Authoritative reviewed question-answer knowledge with retrieval lifecycle."""

    __tablename__ = "qa_knowledge"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_qa_knowledge_scope_id"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_qa_knowledge_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "source_document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_qa_knowledge_scope_document",
        ),
        CheckConstraint("revision > 0", name="ck_qa_knowledge_revision_positive"),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name="ck_qa_knowledge_review_status",
        ),
        CheckConstraint(
            "lifecycle_state IN ('active', 'expired', 'recycled', 'delete_requested', "
            "'deleting', 'delete_failed', 'deleted')",
            name="ck_qa_knowledge_lifecycle_state",
        ),
        CheckConstraint(
            "lifecycle_state = 'active' OR NOT retrieval_enabled",
            name="ck_qa_knowledge_retrieval_lifecycle",
        ),
        CheckConstraint(
            "review_status = 'approved' OR NOT retrieval_enabled",
            name="ck_qa_knowledge_review_retrieval",
        ),
        CheckConstraint("origin IN ('manual', 'automatic')", name="ck_qa_knowledge_origin"),
        Index(
            "ix_qa_knowledge_scope_effective",
            "tenant_id",
            "dataset_id",
            "lifecycle_state",
            "retrieval_enabled",
            "effective_from",
            "expires_at",
        ),
        Index(
            "ix_qa_knowledge_scope_review",
            "tenant_id",
            "dataset_id",
            "review_status",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(24), default="manual")
    review_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(24), default="active", index=True)
    retrieval_enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    effective_from: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    expires_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None, index=True)
    source_document_id: Mapped[Optional[str]] = mapped_column(String(64), default=None, index=True)
    source_uri: Mapped[str] = mapped_column(String(1024), default="")
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(64))
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class QAAlternativeQuestion(Base):
    """Normalized alternative phrasing attached to one scoped QA fact."""

    __tablename__ = "qa_alternative_questions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "qa_id",
            "normalized_hash",
            name="uq_qa_alternatives_qa_normalized_hash",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_qa_alternatives_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "qa_id"],
            ["qa_knowledge.tenant_id", "qa_knowledge.dataset_id", "qa_knowledge.id"],
            name="fk_qa_alternatives_scope_qa",
        ),
        Index(
            "ix_qa_alternatives_scope_qa",
            "tenant_id",
            "dataset_id",
            "qa_id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    qa_id: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    normalized_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class KnowledgeAuditEvent(Base):
    """Append-only audit fact for knowledge-governance mutations."""

    __tablename__ = "knowledge_audit_events"
    __table_args__ = (
        UniqueConstraint("id", name="uq_knowledge_audit_events_id"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_knowledge_audit_scope_dataset",
        ),
        Index(
            "ix_knowledge_audit_scope_time",
            "tenant_id",
            "dataset_id",
            "occurred_at",
            "sequence",
        ),
        Index(
            "ix_knowledge_audit_resource_time",
            "resource_type",
            "resource_id",
            "occurred_at",
            "sequence",
        ),
        Index("ix_knowledge_audit_request_id", "request_id"),
    )

    sequence: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    id: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    resource_id: Mapped[str] = mapped_column(String(512), index=True)
    before_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    after_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    request_id: Mapped[str] = mapped_column(String(128), default="")
    request_ip: Mapped[str] = mapped_column(String(64), default="")
    occurred_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class TenantAuditEvent(Base):
    """Append-only enterprise audit history with actor snapshots."""

    __tablename__ = "tenant_audit_events"
    __table_args__ = (
        UniqueConstraint("id", name="uq_tenant_audit_events_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_audit_events_tenant",
        ),
        Index(
            "ix_tenant_audit_scope_sequence",
            "tenant_id",
            "sequence",
        ),
        Index(
            "ix_tenant_audit_scope_time",
            "tenant_id",
            "occurred_at",
            "sequence",
        ),
        Index(
            "ix_tenant_audit_actor_time",
            "tenant_id",
            "actor_id",
            "occurred_at",
            "sequence",
        ),
        Index(
            "ix_tenant_audit_resource_time",
            "tenant_id",
            "resource_type",
            "resource_id",
            "occurred_at",
            "sequence",
        ),
        Index(
            "ix_tenant_audit_target_time",
            "tenant_id",
            "target_account_id",
            "occurred_at",
            "sequence",
        ),
        Index("ix_tenant_audit_request", "tenant_id", "request_id"),
    )

    sequence: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    id: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_name_snapshot: Mapped[str] = mapped_column(String(128))
    actor_email_snapshot: Mapped[str] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    resource_id: Mapped[str] = mapped_column(String(512), index=True)
    target_account_id: Mapped[Optional[str]] = mapped_column(String(64), default=None, index=True)
    before_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    after_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    request_id: Mapped[str] = mapped_column(String(128), default="")
    request_ip: Mapped[str] = mapped_column(String(64), default="")
    occurred_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)

    tenant: Mapped["Tenant"] = relationship(back_populates="audit_events")


class RetrievalExperiment(Base):
    """Immutable retrieval strategy/result/evidence snapshot for governed evaluation."""

    __tablename__ = "retrieval_experiments"
    __table_args__ = (
        UniqueConstraint("id", name="uq_retrieval_experiments_id"),
        UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_retrieval_experiments_scope_id"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_retrieval_experiments_scope_dataset",
        ),
        CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_retrieval_experiments_status",
        ),
        CheckConstraint("latency_ms >= 0", name="ck_retrieval_experiments_latency_nonnegative"),
        Index(
            "ix_retrieval_experiments_scope_sequence",
            "tenant_id",
            "dataset_id",
            "sequence",
        ),
        Index(
            "ix_retrieval_experiments_scope_query_hash",
            "tenant_id",
            "dataset_id",
            "query_hash",
            "sequence",
        ),
        Index(
            "ix_retrieval_experiments_scope_status_time",
            "tenant_id",
            "dataset_id",
            "status",
            "created_at",
            "sequence",
        ),
        Index("ix_retrieval_experiments_run_id", "run_id"),
        Index(
            "ix_retrieval_experiments_scope_run",
            "tenant_id",
            "dataset_id",
            "run_id",
            "sequence",
        ),
    )

    sequence: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    id: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    query: Mapped[str] = mapped_column(Text)
    query_hash: Mapped[str] = mapped_column(String(64))
    strategy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence_lineage: Mapped[dict[str, Any]] = mapped_column(JSON)
    latency_ms: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)
    run_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class RetrievalJudgment(Base):
    """Reviewer-owned relevance judgment with optimistic revision fencing."""

    __tablename__ = "retrieval_judgments"
    __table_args__ = (
        UniqueConstraint("id", name="uq_retrieval_judgments_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "experiment_id",
            "result_rank",
            "created_by",
            name="uq_retrieval_judgments_experiment_rank_judge",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_retrieval_judgments_scope_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "experiment_id"],
            [
                "retrieval_experiments.tenant_id",
                "retrieval_experiments.dataset_id",
                "retrieval_experiments.id",
            ],
            name="fk_retrieval_judgments_scope_experiment",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_retrieval_judgments_scope_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "chunk_id"],
            ["chunk_heads.tenant_id", "chunk_heads.dataset_id", "chunk_heads.id"],
            name="fk_retrieval_judgments_scope_chunk",
        ),
        CheckConstraint("result_rank > 0", name="ck_retrieval_judgments_rank_positive"),
        CheckConstraint(
            "relevance_label IN ('relevant', 'partial', 'irrelevant')",
            name="ck_retrieval_judgments_relevance_label",
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 3)", name="ck_retrieval_judgments_score"
        ),
        CheckConstraint("revision > 0", name="ck_retrieval_judgments_revision_positive"),
        Index(
            "ix_retrieval_judgments_scope_experiment_rank",
            "tenant_id",
            "dataset_id",
            "experiment_id",
            "result_rank",
        ),
        Index(
            "ix_retrieval_judgments_scope_document",
            "tenant_id",
            "dataset_id",
            "document_id",
        ),
        Index(
            "ix_retrieval_judgments_scope_chunk",
            "tenant_id",
            "dataset_id",
            "chunk_id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    experiment_id: Mapped[str] = mapped_column(String(64), index=True)
    result_rank: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    chunk_id: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    relevance_label: Mapped[str] = mapped_column(String(16))
    score: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    note: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_datetime6(), default=datetime.utcnow)


class MetadataField(Base):
    """元数据 schema 注册表（manual 过滤与 LLM 自动过滤的共用白名单）。"""

    __tablename__ = "metadata_fields"
    __table_args__ = (UniqueConstraint("dataset_id", "key", name="uq_metadata_fields_dataset_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value_type: Mapped[str] = mapped_column(String(16), default="string")  # string|number|bool|date
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | auto
    label: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = _now()

    dataset: Mapped["Dataset"] = relationship(back_populates="metadata_fields")


__all__ = [
    "Base",
    "DOC_STATUSES",
    "SEGMENT_STATUSES",
    "valid_transition",
    "Account",
    "Tenant",
    "TenantMember",
    "TenantNotificationSubscription",
    "TenantNotification",
    "TenantNotificationRecipient",
    "TenantNotificationReceipt",
    "TenantNotificationEvent",
    "TenantWorkspace",
    "TenantWorkspaceMember",
    "TenantWorkspaceDataset",
    "TenantOrganizationUnit",
    "TenantOrganizationUnitMember",
    "TenantGroup",
    "TenantGroupMember",
    "DatasetAccessGrant",
    "TenantInvitation",
    "DatasetAclMutationRequest",
    "TenantControlMutationRequest",
    "TenantVerifiedDomain",
    "TenantIdentityProvider",
    "TenantScimToken",
    "TenantScimUserLink",
    "TenantScimGroupLink",
    "TenantAuditRetentionPolicy",
    "TenantAuditLegalHold",
    "TenantAuditExportJob",
    "TenantOidcLoginTransaction",
    "TenantOidcSubjectLink",
    "TenantSsoSession",
    "App",
    "Workflow",
    "Dataset",
    "Document",
    "DocumentVersion",
    "DocumentSegment",
    "DocumentIngestAttempt",
    "DocumentIngestSpan",
    "IndexOperation",
    "IndexDeadLetter",
    "DataSourceRecord",
    "SourceSchedule",
    "SourceSyncRun",
    "SourceSyncItem",
    "SourceDocumentState",
    "DocumentDeleteBatch",
    "DocumentDeleteOperation",
    "ChunkHead",
    "ChunkRevision",
    "KnowledgeFolder",
    "KnowledgeTag",
    "DocumentTag",
    "QAKnowledge",
    "QAAlternativeQuestion",
    "KnowledgeAuditEvent",
    "RetrievalExperiment",
    "RetrievalJudgment",
    "MetadataField",
]

# ---------------------------------------------------------------------------
# Enterprise Knowledge Serving & Reliability (Stage 26)
# ---------------------------------------------------------------------------


class TenantKnowledgeServingProfile(Base):
    """Operator-owned Tenant/Dataset serving reliability profile."""

    __tablename__ = "tenant_knowledge_serving_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_knowledge_serving_profiles_scope_id"),
        UniqueConstraint(
            "tenant_id", "dataset_id", name="uq_tenant_knowledge_serving_profiles_dataset_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "active_profile_key",
            name="uq_tenant_knowledge_serving_profiles_active_key",
        ),
        CheckConstraint(
            "status IN ('draft','active','paused','archived')",
            name="ck_tenant_knowledge_serving_profiles_status",
        ),
        CheckConstraint("revision > 0", name="ck_tenant_knowledge_serving_profiles_revision"),
        CheckConstraint(
            "active_profile_key IS NULL OR active_profile_key=dataset_id",
            name="ck_tenant_knowledge_serving_profiles_active_identity",
        ),
        CheckConstraint(
            "(status='active' AND active_profile_key=dataset_id AND current_policy_revision_id IS NOT NULL) OR "
            "(status<>'active' AND active_profile_key IS NULL)",
            name="ck_tenant_knowledge_serving_profiles_active_policy",
        ),
        CheckConstraint(
            "(status='archived' AND active_profile_key IS NULL AND archived_at IS NOT NULL AND archived_by IS NOT NULL) OR "
            "(status<>'archived' AND archived_at IS NULL AND archived_by IS NULL)",
            name="ck_tenant_knowledge_serving_profiles_lifecycle",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_knowledge_serving_profiles_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_knowledge_serving_profiles_workspace",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_knowledge_serving_profiles_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_profiles_creator",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_profiles_updater",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "archived_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_profiles_archiver",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "id", "current_policy_revision_id"],
            [
                "tenant_knowledge_serving_policy_revisions.tenant_id",
                "tenant_knowledge_serving_policy_revisions.profile_id",
                "tenant_knowledge_serving_policy_revisions.id",
            ],
            name="fk_tenant_knowledge_serving_profiles_current_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "id", "current_snapshot_id"],
            [
                "tenant_knowledge_serving_snapshots.tenant_id",
                "tenant_knowledge_serving_snapshots.profile_id",
                "tenant_knowledge_serving_snapshots.id",
            ],
            name="fk_tenant_knowledge_serving_profiles_current_snapshot",
        ),
        Index(
            "ix_tenant_knowledge_serving_profiles_tenant_status_updated",
            "tenant_id",
            "status",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    workspace_id: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    dataset_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="'draft'")
    active_profile_key: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    current_policy_revision_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    current_snapshot_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        _datetime6(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by: Mapped[str] = mapped_column(String(64))
    archived_at: Mapped[Optional[datetime]] = mapped_column(_datetime6(), default=None)
    archived_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class TenantKnowledgeServingPolicyRevision(Base):
    """Immutable bounded evaluation policy for a serving profile."""

    __tablename__ = "tenant_knowledge_serving_policy_revisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_knowledge_serving_policy_revisions_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "profile_id",
            "id",
            name="uq_tenant_knowledge_serving_policy_revisions_profile_scope",
        ),
        UniqueConstraint(
            "tenant_id",
            "profile_id",
            "revision",
            name="uq_tenant_knowledge_serving_policy_revisions_profile_revision",
        ),
        CheckConstraint(
            "revision > 0 AND max_source_staleness_seconds >= 0 AND max_parse_lag_seconds >= 0 AND max_index_lag_seconds >= 0 AND max_failed_document_count >= 0 AND max_pending_index_count >= 0",
            name="ck_tenant_knowledge_serving_policy_revisions_thresholds",
        ),
        CheckConstraint(
            "length(policy_digest)=64 AND lower(policy_digest)=policy_digest",
            name="ck_tenant_knowledge_serving_policy_revisions_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            ["tenant_knowledge_serving_profiles.tenant_id", "tenant_knowledge_serving_profiles.id"],
            name="fk_tenant_knowledge_serving_policy_revisions_profile",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_policy_revisions_creator",
        ),
        Index(
            "ix_tenant_knowledge_serving_policy_revisions_profile_created",
            "tenant_id",
            "profile_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    max_source_staleness_seconds: Mapped[int] = mapped_column(Integer)
    max_parse_lag_seconds: Mapped[int] = mapped_column(Integer)
    max_index_lag_seconds: Mapped[int] = mapped_column(Integer)
    max_failed_document_count: Mapped[int] = mapped_column(Integer)
    max_pending_index_count: Mapped[int] = mapped_column(Integer)
    require_current_release: Mapped[bool] = mapped_column(Boolean)
    require_passing_certification: Mapped[bool] = mapped_column(Boolean)
    policy_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))


class TenantKnowledgeServingSnapshot(Base):
    """Immutable observation snapshot of one profile and policy revision."""

    __tablename__ = "tenant_knowledge_serving_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_knowledge_serving_snapshots_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "profile_id",
            "id",
            name="uq_tenant_knowledge_serving_snapshots_profile_scope",
        ),
        UniqueConstraint(
            "tenant_id",
            "profile_id",
            "observation_key",
            name="uq_tenant_knowledge_serving_snapshots_observation",
        ),
        UniqueConstraint(
            "tenant_id", "snapshot_digest", name="uq_tenant_knowledge_serving_snapshots_digest"
        ),
        CheckConstraint(
            "state IN ('ready','degraded','blocked','unavailable')",
            name="ck_tenant_knowledge_serving_snapshots_state",
        ),
        CheckConstraint(
            "source_count >= 0 AND ready_source_count >= 0 AND stale_source_count >= 0 AND active_document_count >= 0 AND failed_document_count >= 0 AND pending_index_count >= 0 AND expected_serving_generation >= 0 AND observed_serving_generation >= 0 AND stage_count=5 AND ready_stage_count BETWEEN 0 AND 5 AND blocked_stage_count BETWEEN 0 AND 5 AND ready_source_count <= source_count AND stale_source_count <= source_count",
            name="ck_tenant_knowledge_serving_snapshots_counts",
        ),
        CheckConstraint(
            "length(snapshot_digest)=64 AND lower(snapshot_digest)=snapshot_digest",
            name="ck_tenant_knowledge_serving_snapshots_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            ["tenant_knowledge_serving_profiles.tenant_id", "tenant_knowledge_serving_profiles.id"],
            name="fk_tenant_knowledge_serving_snapshots_profile",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "profile_id", "policy_revision_id"],
            [
                "tenant_knowledge_serving_policy_revisions.tenant_id",
                "tenant_knowledge_serving_policy_revisions.profile_id",
                "tenant_knowledge_serving_policy_revisions.id",
            ],
            name="fk_tenant_knowledge_serving_snapshots_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "current_release_id"],
            ["dataset_release_manifests.tenant_id", "dataset_release_manifests.id"],
            name="fk_tenant_knowledge_serving_snapshots_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "current_certification_id"],
            [
                "dataset_release_quality_certifications.tenant_id",
                "dataset_release_quality_certifications.id",
            ],
            name="fk_tenant_knowledge_serving_snapshots_certification",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_snapshots_creator",
        ),
        Index(
            "ix_tenant_knowledge_serving_snapshots_profile_as_of",
            "tenant_id",
            "profile_id",
            "as_of",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(String(64))
    policy_revision_id: Mapped[str] = mapped_column(String(64))
    observation_key: Mapped[str] = mapped_column(String(192))
    state: Mapped[str] = mapped_column(String(16))
    source_count: Mapped[int] = mapped_column(Integer)
    ready_source_count: Mapped[int] = mapped_column(Integer)
    stale_source_count: Mapped[int] = mapped_column(Integer)
    active_document_count: Mapped[int] = mapped_column(Integer)
    failed_document_count: Mapped[int] = mapped_column(Integer)
    pending_index_count: Mapped[int] = mapped_column(Integer)
    expected_serving_generation: Mapped[int] = mapped_column(Integer)
    observed_serving_generation: Mapped[int] = mapped_column(Integer)
    current_release_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    current_certification_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    stage_count: Mapped[int] = mapped_column(Integer)
    ready_stage_count: Mapped[int] = mapped_column(Integer)
    blocked_stage_count: Mapped[int] = mapped_column(Integer)
    snapshot_digest: Mapped[str] = mapped_column(String(64))
    as_of: Mapped[datetime] = mapped_column(_datetime6())
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64))


class TenantKnowledgeServingStageFact(Base):
    """Immutable canonical fact for one of the five serving stages."""

    __tablename__ = "tenant_knowledge_serving_stage_facts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_knowledge_serving_stage_facts_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "snapshot_id",
            "stage_code",
            name="uq_tenant_knowledge_serving_stage_facts_stage",
        ),
        UniqueConstraint(
            "tenant_id",
            "snapshot_id",
            "sequence",
            name="uq_tenant_knowledge_serving_stage_facts_sequence",
        ),
        CheckConstraint(
            "(stage_code='source' AND sequence=1) OR (stage_code='parse' AND sequence=2) OR (stage_code='chunk' AND sequence=3) OR (stage_code='index' AND sequence=4) OR (stage_code='serve' AND sequence=5)",
            name="ck_tenant_knowledge_serving_stage_facts_stage",
        ),
        CheckConstraint(
            "sequence BETWEEN 1 AND 5", name="ck_tenant_knowledge_serving_stage_facts_sequence"
        ),
        CheckConstraint(
            "state IN ('ready','lagging','blocked','missing','unavailable')",
            name="ck_tenant_knowledge_serving_stage_facts_state",
        ),
        CheckConstraint(
            "item_count >= 0 AND ready_count >= 0 AND warning_count >= 0 AND pending_count >= 0 AND error_count >= 0 AND ready_count <= item_count AND warning_count <= item_count AND pending_count <= item_count AND error_count <= item_count AND lag_seconds >= 0 AND (expected_revision IS NULL OR expected_revision > 0) AND (observed_revision IS NULL OR observed_revision > 0)",
            name="ck_tenant_knowledge_serving_stage_facts_counts",
        ),
        CheckConstraint(
            "(safe_error_code IS NULL AND safe_error IS NULL) OR (safe_error_code IS NOT NULL AND safe_error IS NOT NULL)",
            name="ck_tenant_knowledge_serving_stage_facts_error",
        ),
        CheckConstraint(
            "length(stage_digest)=64 AND lower(stage_digest)=stage_digest AND (expected_digest IS NULL OR (length(expected_digest)=64 AND lower(expected_digest)=expected_digest)) AND (observed_digest IS NULL OR (length(observed_digest)=64 AND lower(observed_digest)=observed_digest))",
            name="ck_tenant_knowledge_serving_stage_facts_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            ["tenant_knowledge_serving_profiles.tenant_id", "tenant_knowledge_serving_profiles.id"],
            name="fk_tenant_knowledge_serving_stage_facts_profile",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "profile_id", "snapshot_id"],
            [
                "tenant_knowledge_serving_snapshots.tenant_id",
                "tenant_knowledge_serving_snapshots.profile_id",
                "tenant_knowledge_serving_snapshots.id",
            ],
            name="fk_tenant_knowledge_serving_stage_facts_snapshot",
        ),
        UniqueConstraint(
            "tenant_id",
            "profile_id",
            "snapshot_id",
            "id",
            name="uq_tenant_knowledge_serving_stage_facts_profile_snapshot_id",
        ),
        Index(
            "ix_tenant_knowledge_serving_stage_facts_snapshot_sequence",
            "tenant_id",
            "snapshot_id",
            "sequence",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(String(64))
    snapshot_id: Mapped[str] = mapped_column(String(64))
    stage_code: Mapped[str] = mapped_column(String(16))
    sequence: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16))
    item_count: Mapped[int] = mapped_column(Integer)
    ready_count: Mapped[int] = mapped_column(Integer)
    warning_count: Mapped[int] = mapped_column(Integer)
    pending_count: Mapped[int] = mapped_column(Integer)
    error_count: Mapped[int] = mapped_column(Integer)
    lag_seconds: Mapped[int] = mapped_column(Integer)
    expected_revision: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    observed_revision: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    expected_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    observed_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    safe_error_code: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    safe_error: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    stage_digest: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(_datetime6())


class TenantKnowledgeServingEvidenceLink(Base):
    """Immutable bounded reference to an existing internal authority."""

    __tablename__ = "tenant_knowledge_serving_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_knowledge_serving_evidence_links_scope_id"
        ),
        UniqueConstraint(
            "tenant_id", "evidence_digest", name="uq_tenant_knowledge_serving_evidence_links_digest"
        ),
        CheckConstraint(
            "evidence_kind IN ('source','source_sync_run','document','ingest_attempt','chunk_head','index_operation','release','certification','task')",
            name="ck_tenant_knowledge_serving_evidence_links_kind",
        ),
        CheckConstraint(
            "route_code IN ('knowledge_sources','knowledge_documents','knowledge_indexing','knowledge_base_releases','release_quality','enterprise_tasks')",
            name="ck_tenant_knowledge_serving_evidence_links_route",
        ),
        CheckConstraint(
            " OR ".join(
                f"(evidence_kind='{kind}' AND route_code='{route}')"
                for kind, route in (
                    ("source", "knowledge_sources"),
                    ("source_sync_run", "knowledge_sources"),
                    ("document", "knowledge_documents"),
                    ("ingest_attempt", "knowledge_documents"),
                    ("chunk_head", "knowledge_documents"),
                    ("index_operation", "knowledge_indexing"),
                    ("release", "knowledge_base_releases"),
                    ("certification", "release_quality"),
                    ("task", "enterprise_tasks"),
                )
            ),
            name="ck_tenant_knowledge_serving_evidence_links_kind_route",
        ),
        CheckConstraint(
            "resource_id <> '' AND (resource_revision IS NULL OR resource_revision >= 1) AND (resource_digest IS NULL OR (length(resource_digest)=64 AND lower(resource_digest)=resource_digest)) AND length(evidence_digest)=64 AND lower(evidence_digest)=evidence_digest",
            name="ck_tenant_knowledge_serving_evidence_links_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            ["tenant_knowledge_serving_profiles.tenant_id", "tenant_knowledge_serving_profiles.id"],
            name="fk_tenant_knowledge_serving_evidence_links_profile",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "profile_id", "snapshot_id"],
            [
                "tenant_knowledge_serving_snapshots.tenant_id",
                "tenant_knowledge_serving_snapshots.profile_id",
                "tenant_knowledge_serving_snapshots.id",
            ],
            name="fk_tenant_knowledge_serving_evidence_links_snapshot",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "profile_id", "snapshot_id", "stage_fact_id"],
            [
                "tenant_knowledge_serving_stage_facts.tenant_id",
                "tenant_knowledge_serving_stage_facts.profile_id",
                "tenant_knowledge_serving_stage_facts.snapshot_id",
                "tenant_knowledge_serving_stage_facts.id",
            ],
            name="fk_tenant_knowledge_serving_evidence_links_stage_fact",
        ),
        Index(
            "ix_tenant_knowledge_serving_evidence_links_snapshot_kind",
            "tenant_id",
            "snapshot_id",
            "evidence_kind",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(String(64))
    snapshot_id: Mapped[str] = mapped_column(String(64))
    stage_fact_id: Mapped[str] = mapped_column(String(64))
    evidence_kind: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(String(128))
    resource_revision: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    resource_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    route_code: Mapped[str] = mapped_column(String(64))
    safe_label: Mapped[str] = mapped_column(String(256))
    evidence_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        _datetime6(), default=datetime.utcnow
    )


class TenantKnowledgeServingEvent(Base):
    """Immutable per-profile serving reliability event chain."""

    __tablename__ = "tenant_knowledge_serving_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_knowledge_serving_events_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "stream_key",
            "sequence",
            name="uq_tenant_knowledge_serving_events_stream_sequence",
        ),
        UniqueConstraint(
            "tenant_id", "event_digest", name="uq_tenant_knowledge_serving_events_digest"
        ),
        CheckConstraint(
            "event_type IN ('profile_created','policy_revision_created','policy_activated','snapshot_recorded','stage_degraded','stage_blocked','service_recovered')",
            name="ck_tenant_knowledge_serving_events_type",
        ),
        CheckConstraint("sequence > 0", name="ck_tenant_knowledge_serving_events_sequence"),
        CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest AND (previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND lower(previous_event_digest)=previous_event_digest))",
            name="ck_tenant_knowledge_serving_events_digest",
        ),
        CheckConstraint(
            "(sequence=1 AND previous_event_digest IS NULL) OR (sequence>1 AND previous_event_digest IS NOT NULL)",
            name="ck_tenant_knowledge_serving_events_hash_chain",
        ),
        CheckConstraint(
            "safe_snapshot_json IS NOT NULL", name="ck_tenant_knowledge_serving_events_snapshot"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            ["tenant_knowledge_serving_profiles.tenant_id", "tenant_knowledge_serving_profiles.id"],
            name="fk_tenant_knowledge_serving_events_profile",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "profile_id", "snapshot_id"],
            [
                "tenant_knowledge_serving_snapshots.tenant_id",
                "tenant_knowledge_serving_snapshots.profile_id",
                "tenant_knowledge_serving_snapshots.id",
            ],
            name="fk_tenant_knowledge_serving_events_snapshot",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_events_actor",
        ),
        Index(
            "ix_tenant_knowledge_serving_events_profile_time",
            "tenant_id",
            "profile_id",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(String(64))
    snapshot_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    stream_key: Mapped[str] = mapped_column(String(128))
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    previous_event_digest: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    event_digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128))
    safe_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(_datetime6())


__all__.extend(
    [
        "TenantKnowledgeServingProfile",
        "TenantKnowledgeServingPolicyRevision",
        "TenantKnowledgeServingSnapshot",
        "TenantKnowledgeServingStageFact",
        "TenantKnowledgeServingEvidenceLink",
        "TenantKnowledgeServingEvent",
    ]
)
