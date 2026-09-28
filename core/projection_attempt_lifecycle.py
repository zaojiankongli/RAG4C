"""Per-store policies for index-attempt readiness and finalization barriers."""
from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from core.providers import ProviderRegistry

_STORE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


@dataclass(frozen=True)
class ProjectionAttemptReadinessContext:
    attempt_id: str
    target_store: str
    target_revision: int
    document_id: str
    content_revision: int
    desired_index_revision: int
    indexed_revision: int
    graph_revision: int


@dataclass(frozen=True)
class ProjectionAttemptLifecyclePolicy:
    role: Literal["primary", "secondary"]
    blocks_finalization: bool
    is_ready: Callable[[ProjectionAttemptReadinessContext], bool]


class InvalidProjectionAttemptTarget(ValueError):
    """A target_store value is not a canonical lowercase code."""


PROJECTION_ATTEMPT_LIFECYCLE_POLICIES: ProviderRegistry[
    None, ProjectionAttemptLifecyclePolicy
] = ProviderRegistry("projection attempt lifecycle policy")


def _validate_target_store(target_store: str) -> None:
    if not isinstance(target_store, str) or _STORE_CODE.fullmatch(target_store) is None:
        raise InvalidProjectionAttemptTarget("target_store must be a lowercase code")


def _validate_readiness_callback(callback: object) -> None:
    if not callable(callback):
        raise TypeError("projection attempt policy is_ready must be callable")
    callable_methods = (callback, getattr(callback, "__call__", None))
    if any(
        inspect.iscoroutinefunction(method)
        or inspect.isasyncgenfunction(method)
        or inspect.isgeneratorfunction(method)
        for method in callable_methods
        if method is not None
    ):
        raise TypeError("projection attempt policy is_ready must be synchronous")
    try:
        inspect.signature(callback).bind(object())
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "projection attempt policy is_ready must accept one positional context"
        ) from exc


def register_projection_attempt_lifecycle_policy(
    target_store: str,
    policy: ProjectionAttemptLifecyclePolicy,
    *,
    replace: bool = False,
) -> None:
    _validate_target_store(target_store)
    if not isinstance(policy, ProjectionAttemptLifecyclePolicy):
        raise TypeError("projection attempt lifecycle policy must be a policy instance")
    if policy.role not in {"primary", "secondary"}:
        raise ValueError("projection attempt lifecycle policy role must be primary or secondary")
    if not isinstance(policy.blocks_finalization, bool):
        raise TypeError("projection attempt policy blocks_finalization must be bool")
    _validate_readiness_callback(policy.is_ready)

    def policy_factory(_context: None) -> ProjectionAttemptLifecyclePolicy:
        return policy

    PROJECTION_ATTEMPT_LIFECYCLE_POLICIES.register(
        target_store, policy_factory, replace=replace
    )


def unregister_projection_attempt_lifecycle_policy(target_store: str) -> None:
    _validate_target_store(target_store)
    PROJECTION_ATTEMPT_LIFECYCLE_POLICIES.unregister(target_store)


def resolve_projection_attempt_lifecycle_policy(
    target_store: str,
) -> ProjectionAttemptLifecyclePolicy:
    _validate_target_store(target_store)
    return PROJECTION_ATTEMPT_LIFECYCLE_POLICIES.create(target_store, None)


def invoke_projection_attempt_readiness(
    policy: ProjectionAttemptLifecyclePolicy,
    context: ProjectionAttemptReadinessContext,
) -> bool:
    result = policy.is_ready(context)
    if inspect.isawaitable(result) or inspect.isgenerator(result) or inspect.isasyncgen(result):
        cancel = getattr(result, "cancel", None)
        if callable(cancel):
            cancel()
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise TypeError("projection attempt policy is_ready must be synchronous")
    if not isinstance(result, bool):
        raise TypeError("projection attempt policy is_ready must return bool")
    return result

def _milvus_ready(context: ProjectionAttemptReadinessContext) -> bool:
    if context.target_revision < context.desired_index_revision:
        return True
    return context.desired_index_revision == context.indexed_revision


def _graph_ready(context: ProjectionAttemptReadinessContext) -> bool:
    return context.graph_revision == context.content_revision


register_projection_attempt_lifecycle_policy(
    "milvus_chunks",
    ProjectionAttemptLifecyclePolicy(
        role="primary", blocks_finalization=False, is_ready=_milvus_ready
    ),
    replace=True,
)
register_projection_attempt_lifecycle_policy(
    "graph_projection",
    ProjectionAttemptLifecyclePolicy(
        role="secondary", blocks_finalization=True, is_ready=_graph_ready
    ),
    replace=True,
)


__all__ = [
    "InvalidProjectionAttemptTarget",
    "ProjectionAttemptLifecyclePolicy",
    "ProjectionAttemptReadinessContext",
    "PROJECTION_ATTEMPT_LIFECYCLE_POLICIES",
    "invoke_projection_attempt_readiness",
    "register_projection_attempt_lifecycle_policy",
    "resolve_projection_attempt_lifecycle_policy",
    "unregister_projection_attempt_lifecycle_policy",
]
