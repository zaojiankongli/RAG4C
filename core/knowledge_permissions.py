"""Fail-closed KnowledgeOps permission vocabulary and role policy."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

KNOWLEDGE_READ: Final = "knowledge.read"
KNOWLEDGE_WRITE: Final = "knowledge.write"
KNOWLEDGE_DELETE: Final = "knowledge.delete"
KNOWLEDGE_MANAGE: Final = "knowledge.manage"
KNOWLEDGE_AUDIT: Final = "knowledge.audit"

KNOWLEDGE_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        KNOWLEDGE_READ,
        KNOWLEDGE_WRITE,
        KNOWLEDGE_DELETE,
        KNOWLEDGE_MANAGE,
        KNOWLEDGE_AUDIT,
    }
)

_FULL_ACCESS = KNOWLEDGE_PERMISSIONS
_ROLE_PERMISSIONS = MappingProxyType(
    {
        "owner": _FULL_ACCESS,
        "admin": _FULL_ACCESS,
        "editor": frozenset({KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_DELETE}),
        "member": frozenset({KNOWLEDGE_READ}),
    }
)


def permissions_for_role(role: str) -> frozenset[str]:
    """Return the immutable permission set for a persisted tenant role.

    Unknown, empty, and malformed roles intentionally receive no permissions.
    """

    normalized = str(role or "").strip().casefold()
    return _ROLE_PERMISSIONS.get(normalized, frozenset())


def role_allows(role: str, permission: str) -> bool:
    """Return whether ``role`` grants a known KnowledgeOps permission."""

    return permission in KNOWLEDGE_PERMISSIONS and permission in permissions_for_role(role)


def validate_knowledge_permission(permission: str) -> str:
    """Validate a permission at dependency construction time."""

    if permission not in KNOWLEDGE_PERMISSIONS:
        raise ValueError(f"unknown KnowledgeOps permission: {permission!r}")
    return permission


__all__ = [
    "KNOWLEDGE_AUDIT",
    "KNOWLEDGE_DELETE",
    "KNOWLEDGE_MANAGE",
    "KNOWLEDGE_PERMISSIONS",
    "KNOWLEDGE_READ",
    "KNOWLEDGE_WRITE",
    "permissions_for_role",
    "role_allows",
    "validate_knowledge_permission",
]
