"""Registered target-store selection for durable projection production."""

from __future__ import annotations

import inspect
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass

from core.providers import ProviderRegistry

_STORE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_DEDUP_COMPONENT = re.compile(r"[a-z][a-z0-9_]{0,63}")
_LEGACY_DEDUP_COMPONENTS = {"milvus_chunks": "milvus", "graph_projection": "graph"}
_RESERVED_DEDUP_COMPONENTS = frozenset(_LEGACY_DEDUP_COMPONENTS.values())


@dataclass(frozen=True)
class ProjectionTargetProductionContext:
    include_graph: bool


@dataclass(frozen=True)
class ProjectionTargetPolicy:
    order: int
    dedup_key_component: str
    enabled: Callable[[ProjectionTargetProductionContext], bool]


@dataclass(frozen=True)
class ProjectionTarget:
    target_store: str
    dedup_key_component: str
    order: int


class InvalidProjectionTarget(ValueError):
    """A target key or policy isn't a canonical production declaration."""


PROJECTION_TARGET_PRODUCERS: ProviderRegistry[None, ProjectionTargetPolicy] = ProviderRegistry(
    "projection target producer"
)
_POLICY_REGISTRATION_LOCK = threading.RLock()


def _validate_dedup_component(target_store: str, component: object) -> None:
    if type(component) is not str or _DEDUP_COMPONENT.fullmatch(component) is None:
        raise InvalidProjectionTarget(
            "projection target dedup key component must be a canonical string code"
        )
    if target_store not in _LEGACY_DEDUP_COMPONENTS and component in _RESERVED_DEDUP_COMPONENTS:
        raise InvalidProjectionTarget(
            f"projection target dedup key component {component!r} is reserved"
        )
    expected = _LEGACY_DEDUP_COMPONENTS.get(target_store, target_store)
    if component != expected:
        raise ValueError(
            "projection target dedup key component must match target_store "
            f"{target_store!r}; expected {expected!r}"
        )


def _validate_enabled(callback: object) -> None:
    if not callable(callback):
        raise TypeError("projection target enabled predicate must be callable")
    methods = (callback, getattr(callback, "__call__", None))
    if any(
        inspect.iscoroutinefunction(method)
        or inspect.isasyncgenfunction(method)
        or inspect.isgeneratorfunction(method)
        for method in methods
        if method is not None
    ):
        raise TypeError("projection target enabled predicate must be synchronous")
    try:
        inspect.signature(callback).bind(object())
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "projection target enabled predicate must accept one positional context"
        ) from exc


def register_projection_target_policy(
    target_store: str,
    policy: ProjectionTargetPolicy,
    *,
    replace: bool = False,
) -> None:
    if type(target_store) is not str or _STORE_CODE.fullmatch(target_store) is None:
        raise ValueError("projection target_store must be a lowercase code")
    if target_store in _LEGACY_DEDUP_COMPONENTS:
        raise InvalidProjectionTarget("built-in projection target policies are immutable")
    if not isinstance(policy, ProjectionTargetPolicy):
        raise TypeError("projection target policy must be a ProjectionTargetPolicy")
    if type(policy.order) is not int or policy.order < 0:
        raise ValueError("projection target policy order must be a non-negative integer")
    _validate_dedup_component(target_store, policy.dedup_key_component)
    _validate_enabled(policy.enabled)

    def policy_factory(_context: None) -> ProjectionTargetPolicy:
        return policy

    with _POLICY_REGISTRATION_LOCK:
        for existing_store in PROJECTION_TARGET_PRODUCERS.names():
            if existing_store == target_store:
                continue
            existing = PROJECTION_TARGET_PRODUCERS.create(existing_store, None)
            if existing.dedup_key_component == policy.dedup_key_component:
                raise ValueError(
                    "projection target dedup key component already registered: "
                    f"{policy.dedup_key_component!r} for {existing_store!r}"
                )
        PROJECTION_TARGET_PRODUCERS.register(target_store, policy_factory, replace=replace)


def unregister_projection_target_policy(target_store: str) -> None:
    if type(target_store) is not str or _STORE_CODE.fullmatch(target_store) is None:
        raise ValueError("projection target_store must be a lowercase code")
    if target_store in _LEGACY_DEDUP_COMPONENTS:
        raise InvalidProjectionTarget("built-in projection target policies cannot be unregistered")
    with _POLICY_REGISTRATION_LOCK:
        PROJECTION_TARGET_PRODUCERS.unregister(target_store)


def _enabled(policy: ProjectionTargetPolicy, context: ProjectionTargetProductionContext) -> bool:
    result = policy.enabled(context)
    if inspect.isawaitable(result) or inspect.isgenerator(result) or inspect.isasyncgen(result):
        cancel = getattr(result, "cancel", None)
        if callable(cancel):
            cancel()
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise TypeError("projection target enabled predicate must be synchronous")
    if not isinstance(result, bool):
        raise TypeError("projection target enabled predicate must return bool")
    return result


def projection_targets(
    context: ProjectionTargetProductionContext,
) -> tuple[ProjectionTarget, ...]:
    with _POLICY_REGISTRATION_LOCK:
        registered_names = tuple(sorted(PROJECTION_TARGET_PRODUCERS.names()))
        policies = {
            target_store: PROJECTION_TARGET_PRODUCERS.create(target_store, None)
            for target_store in registered_names
        }

    missing_builtins = set(_LEGACY_DEDUP_COMPONENTS).difference(policies)
    if missing_builtins:
        raise InvalidProjectionTarget(
            f"required built-in projection policies are missing: {sorted(missing_builtins)!r}"
        )
    for target_store, expected_policy in _BUILTIN_TARGET_POLICIES.items():
        actual_policy = policies[target_store]
        policy_matches_builtin = (
            type(actual_policy) is ProjectionTargetPolicy
            and type(actual_policy.order) is int
            and actual_policy.order == expected_policy.order
            and type(actual_policy.dedup_key_component) is str
            and actual_policy.dedup_key_component == expected_policy.dedup_key_component
            and actual_policy.enabled is expected_policy.enabled
        )
        if not policy_matches_builtin:
            raise InvalidProjectionTarget(
                f"built-in projection target policy {target_store!r} was replaced"
            )

    targets: list[ProjectionTarget] = []
    for target_store, policy in policies.items():
        if _STORE_CODE.fullmatch(target_store) is None:
            raise InvalidProjectionTarget("registered target_store must be a lowercase code")
        if type(policy) is not ProjectionTargetPolicy:
            raise InvalidProjectionTarget("registry entry must be a ProjectionTargetPolicy")
        if type(policy.order) is not int or policy.order < 0:
            raise InvalidProjectionTarget("registered target order must be a non-negative integer")
        _validate_dedup_component(target_store, policy.dedup_key_component)
        if not _enabled(policy, context):
            continue
        targets.append(
            ProjectionTarget(
                target_store=target_store,
                dedup_key_component=policy.dedup_key_component,
                order=policy.order,
            )
        )
    return tuple(sorted(targets, key=lambda target: (target.order, target.target_store)))


def _always_enabled(_context: ProjectionTargetProductionContext) -> bool:
    return True


def _graph_enabled(context: ProjectionTargetProductionContext) -> bool:
    return context.include_graph


_MILVUS_POLICY = ProjectionTargetPolicy(
    order=10, dedup_key_component="milvus", enabled=_always_enabled
)
_GRAPH_POLICY = ProjectionTargetPolicy(
    order=20, dedup_key_component="graph", enabled=_graph_enabled
)
_BUILTIN_TARGET_POLICIES = {
    "milvus_chunks": _MILVUS_POLICY,
    "graph_projection": _GRAPH_POLICY,
}

for _target_store, _policy in _BUILTIN_TARGET_POLICIES.items():

    def _builtin_factory(
        _context: None, *, _policy: ProjectionTargetPolicy = _policy
    ) -> ProjectionTargetPolicy:
        return _policy

    PROJECTION_TARGET_PRODUCERS.register(_target_store, _builtin_factory)

__all__ = [
    "InvalidProjectionTarget",
    "ProjectionTarget",
    "ProjectionTargetPolicy",
    "ProjectionTargetProductionContext",
    "PROJECTION_TARGET_PRODUCERS",
    "projection_targets",
    "register_projection_target_policy",
    "unregister_projection_target_policy",
]
