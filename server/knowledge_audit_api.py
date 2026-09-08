"""Authenticated, tenant-scoped KnowledgeOps audit query API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from core import catalog
from core.knowledge_governance import (
    KnowledgeGovernanceRepository,
    sanitize_audit_snapshot,
)
from core.knowledge_permissions import KNOWLEDGE_AUDIT
from models.orm import KnowledgeAuditEvent
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset

router = APIRouter(prefix="/api/knowledge-bases/{dataset_id}", tags=["knowledge-audit"])

_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
_AUDIT = require_knowledge_permission(KNOWLEDGE_AUDIT, resolve_path_dataset("dataset_id"))
AuditActor = Annotated[KnowledgeActor, Depends(_AUDIT)]


def _repository() -> KnowledgeGovernanceRepository:
    return KnowledgeGovernanceRepository(catalog.get_engine())


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _event_payload(event: KnowledgeAuditEvent) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "id": event.id,
        "tenant_id": event.tenant_id,
        "dataset_id": event.dataset_id,
        "actor_id": event.actor_id,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "before_snapshot": sanitize_audit_snapshot(event.before_snapshot),
        "after_snapshot": sanitize_audit_snapshot(event.after_snapshot),
        "request_id": event.request_id,
        "request_ip": event.request_ip,
        "occurred_at": event.occurred_at,
    }


@router.get("/audit-events")
def list_audit_events(
    dataset_id: DatasetId,
    actor: AuditActor,
    actor_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    action: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    resource_type: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    resource_id: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    request_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    before_sequence: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    start = _utc_naive(occurred_from)
    end = _utc_naive(occurred_to)
    if start is not None and end is not None and start > end:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail={
                "code": "knowledge_audit_invalid",
                "message": "occurred_from 不得晚于 occurred_to",
            },
        )

    repository = _repository()
    rows = repository.list_audit_events(
        actor.tenant_id,
        dataset_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        occurred_from=start,
        occurred_to=end,
        before_sequence=before_sequence,
        limit=limit + 1,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "items": [_event_payload(item) for item in page],
        "count": len(page),
        "next_before_sequence": page[-1].sequence if has_more and page else None,
    }


__all__ = ["router"]
